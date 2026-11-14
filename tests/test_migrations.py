"""マイグレーション(Alembic)のテスト。一時ファイルの SQLite で、起動時の2つの分かれ道と baseline の中身を確かめる。"""
import pytest
from sqlalchemy import create_engine, inspect, text

from app.db import Base
from app.db_migrate import BASELINE_TABLES, run_migrations


@pytest.fixture()
def tmp_engine(tmp_path):
    # フォルダ名に % を入れておく:URL に % があっても壊れないこと(configparser を通していないこと)の確認
    d = tmp_path / "100%data"
    d.mkdir()
    e = create_engine(f"sqlite:///{d}/m.db")
    yield e
    e.dispose()


def _version(engine) -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT version_num FROM alembic_version"))]


def _shape(engine) -> dict:
    """テーブルごとの 列(名前・NULL可否・主キー)/インデックス/UNIQUE/外部キー。型は DB ごとに書き方が違うので、長さまで文字列で比べる。"""
    insp, out = inspect(engine), {}
    for t in sorted(set(insp.get_table_names()) - {"alembic_version"}):
        out[t] = {
            "columns": {c["name"]: (str(c["type"]), c["nullable"], c["primary_key"]) for c in insp.get_columns(t)},
            "indexes": {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in insp.get_indexes(t)},
            "uniques": sorted(tuple(u["column_names"]) for u in insp.get_unique_constraints(t)),
            "fks": sorted((tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                          for f in insp.get_foreign_keys(t)),
        }
    return out


def test_empty_db_is_upgraded(tmp_engine):
    """(b) 空の DB → upgrade head でテーブルが作られ、版が記録される。"""
    assert run_migrations(tmp_engine) == "upgrade"
    assert BASELINE_TABLES <= set(inspect(tmp_engine).get_table_names())
    assert _version(tmp_engine) == ["0001"]
    assert run_migrations(tmp_engine) == "upgrade"  # 2回目は何もしない(同じ結果)
    assert _version(tmp_engine) == ["0001"]


def test_existing_db_is_stamped_without_changes(tmp_engine):
    """(a) Alembic 導入前の DB(テーブルあり・alembic_version なし)→ 印だけ付ける。中身は変えない。"""
    Base.metadata.create_all(tmp_engine)  # これまでの起動時の動き(本番の今の状態)を再現
    with tmp_engine.begin() as c:
        c.execute(text("INSERT INTO users (id, auth_uid, name, created_at) VALUES ('u1', 'uid1', 'アリス', '2026-11-14 00:00:00')"))
    before = _shape(tmp_engine)

    assert run_migrations(tmp_engine) == "stamp"
    assert _version(tmp_engine) == ["0001"]
    assert _shape(tmp_engine) == before
    with tmp_engine.connect() as c:
        assert c.execute(text("SELECT name FROM users")).scalar() == "アリス"
    assert run_migrations(tmp_engine) == "upgrade"  # 次の起動からは普通の道(何もしない)
    assert _shape(tmp_engine) == before


def test_partial_db_is_refused(tmp_engine):
    """テーブルが一部しか無い DB には印を付けない(足りないテーブルが作られないままになるのを防ぐ)。"""
    Base.metadata.tables["users"].create(tmp_engine)
    with pytest.raises(RuntimeError):
        run_migrations(tmp_engine)
    assert "alembic_version" not in inspect(tmp_engine).get_table_names()


def test_baseline_matches_models(tmp_engine, tmp_path):
    """baseline が作るものと、モデル(create_all)が作るものが同じ:テーブル・列・インデックス・UNIQUE・外部キー。"""
    run_migrations(tmp_engine)
    by_models = create_engine(f"sqlite:///{tmp_path}/models.db")
    Base.metadata.create_all(by_models)
    migrated, modeled = _shape(tmp_engine), _shape(by_models)
    by_models.dispose()
    assert set(migrated) == set(modeled) == set(Base.metadata.tables) == BASELINE_TABLES
    for t in modeled:
        assert set(migrated[t]["columns"]) == set(modeled[t]["columns"]) == {c.name for c in Base.metadata.tables[t].columns}, t
        assert migrated[t] == modeled[t], t


def test_models_and_migrations_are_in_sync(tmp_engine):
    """モデルを変えたのにマイグレーションを足し忘れたら、ここで落ちる(autogenerate の差分が空であること)。"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    run_migrations(tmp_engine)
    with tmp_engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == []
