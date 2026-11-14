"""マイグレーション(Alembic)のテスト。一時ファイルの SQLite で、起動時の2つの分かれ道とマイグレーションの中身を確かめる。"""
import io

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import settings
from app.db import Base
from app.db_migrate import BASELINE_TABLES, ROOT, _config, run_migrations

HEAD = "0002"  # いちばん新しいマイグレーション。足したらここも変える
NEW_TABLES_0002 = {"images", "negotiations", "notifications"}


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


def _upgrade_to(engine, revision: str) -> None:
    """指定した版まで進める(「0001 までしか適用していない DB」を作るため)。"""
    with engine.begin() as c:
        command.upgrade(_config(c), revision)


def _insert_user_and_item(engine) -> None:
    """0001 の時点の列だけで、利用者と商品を 1 行ずつ入れる。"""
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, auth_uid, name, created_at) VALUES ('u1', 'uid1', 'アリス', '2026-11-14 00:00:00')"))
        c.execute(text("INSERT INTO items (id, seller_id, title, description, category, `condition`, price, status, created_at) "
                       "VALUES ('i1', 'u1', '絵本', '説明', 'Kids', 'Good', 1000, 'on_sale', '2026-11-14 00:00:00')"))


def test_empty_db_is_upgraded(tmp_engine):
    """(b) 空の DB → upgrade head でテーブルが作られ、版が記録される。"""
    assert run_migrations(tmp_engine) == "upgrade"
    assert BASELINE_TABLES | NEW_TABLES_0002 <= set(inspect(tmp_engine).get_table_names())
    assert _version(tmp_engine) == [HEAD]
    assert run_migrations(tmp_engine) == "upgrade"  # 2回目は何もしない(同じ結果)
    assert _version(tmp_engine) == [HEAD]


def test_existing_db_is_stamped_then_upgraded(tmp_engine):
    """(a) Alembic 導入前の DB(baseline のテーブルあり・alembic_version なし)
    → baseline は実行せず印だけ付け、そのあとの分(0002)だけを実行する。今あるデータは残る。"""
    _upgrade_to(tmp_engine, "0001")
    with tmp_engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))  # Alembic を入れる前の本番と同じ状態にする
    _insert_user_and_item(tmp_engine)
    before = _shape(tmp_engine)

    assert run_migrations(tmp_engine) == "stamp"
    assert _version(tmp_engine) == [HEAD]
    after = _shape(tmp_engine)
    for t in BASELINE_TABLES - {"items"}:  # 0002 が触らないテーブルは、そのまま
        assert after[t] == before[t], t
    assert set(after["items"]["columns"]) - set(before["items"]["columns"]) == {"min_price", "image_ai_generated"}
    assert set(after) - set(before) == NEW_TABLES_0002
    with tmp_engine.connect() as c:
        assert c.execute(text("SELECT name FROM users")).scalar() == "アリス"
        # 前からあった商品:最低価格は未設定(NULL)、AI 生成の印は false(NOT NULL の列に既定値が入る)
        assert c.execute(text("SELECT title, min_price, image_ai_generated FROM items")).one() == ("絵本", None, 0)
    assert run_migrations(tmp_engine) == "upgrade"  # 次の起動からは普通の道(何もしない)
    assert _shape(tmp_engine) == after


def test_0002_can_be_rerun_after_a_partial_failure(tmp_engine):
    """MySQL は DDL を取り消せないので、0002 が途中で失敗すると「一部だけ適用済み・版は 0001」の DB が残る。
    その状態からもう一度実行して、最後まで進むこと(「もうある」で止まらないこと)。"""
    _upgrade_to(tmp_engine, "0001")
    _insert_user_and_item(tmp_engine)
    with tmp_engine.begin() as c:  # 0002 の前半だけが適用された状態を作る(images はインデックスを作る前に止まった想定)
        c.execute(text("CREATE TABLE images (id VARCHAR(36) NOT NULL, owner_id VARCHAR(36) NOT NULL, content_type VARCHAR(20) NOT NULL, "
                       "data BLOB NOT NULL, kind VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, "
                       "PRIMARY KEY (id), FOREIGN KEY(owner_id) REFERENCES users (id))"))
        c.execute(text("ALTER TABLE items ADD COLUMN min_price INTEGER"))
    assert _version(tmp_engine) == ["0001"]

    assert run_migrations(tmp_engine) == "upgrade"
    assert _version(tmp_engine) == [HEAD]
    fresh = create_engine(f"sqlite:///{tmp_engine.url.database}.fresh")
    run_migrations(fresh)
    assert _shape(tmp_engine) == _shape(fresh)  # 空の DB から作ったものと同じ形になっている
    fresh.dispose()


def test_downgrade_0002_restores_baseline(tmp_engine):
    """0002 を 1 つ戻すと、0001 の時点の形に戻る。"""
    _upgrade_to(tmp_engine, "0001")
    before = _shape(tmp_engine)
    run_migrations(tmp_engine)
    with tmp_engine.begin() as c:
        command.downgrade(_config(c), "0001")
    assert _version(tmp_engine) == ["0001"]
    assert _shape(tmp_engine) == before


def test_0002_sql_for_mysql(monkeypatch):
    """本番(MySQL)に流れる SQL を、DB につながずに出力して確かめる(alembic upgrade 0001:0002 --sql と同じ)。
    手元に MySQL が無いので、実行はしていない。確かめているのは SQL の文面だけ。"""
    monkeypatch.setattr(settings, "database_url", "mysql+pymysql://u:p@localhost/nego")
    out = io.StringIO()
    cfg = Config(output_buffer=out)
    cfg.set_main_option("script_location", str(ROOT / "migrations").replace("%", "%%"))
    command.upgrade(cfg, "0001:0002", sql=True)
    sql = out.getvalue()
    assert "data MEDIUMBLOB NOT NULL" in sql  # 2MB の写真が入る型(BLOB は 64KB まで)
    assert "ALTER TABLE items ADD COLUMN min_price INTEGER;" in sql  # NULL 可
    assert "ALTER TABLE items ADD COLUMN image_ai_generated BOOL NOT NULL DEFAULT false;" in sql  # 行があっても足せる
    assert sql.count("ENGINE=InnoDB") == 3 and sql.count("CHARSET=utf8mb4") == 3
    for line in sql.splitlines():  # MySQL は BLOB / TEXT の列に既定値を付けられない
        if "BLOB" in line or " TEXT" in line:
            assert "DEFAULT" not in line, line
    # 新しいテーブルを先に作り、既存のテーブル(items)の変更は最後
    assert sql.index("CREATE TABLE notifications") < sql.index("ALTER TABLE items")
    assert "UPDATE alembic_version SET version_num='0002'" in sql


def test_partial_db_is_refused(tmp_engine):
    """テーブルが一部しか無い DB には印を付けない(足りないテーブルが作られないままになるのを防ぐ)。"""
    Base.metadata.tables["users"].create(tmp_engine)
    with pytest.raises(RuntimeError):
        run_migrations(tmp_engine)
    assert "alembic_version" not in inspect(tmp_engine).get_table_names()


def test_migrations_match_models(tmp_engine, tmp_path):
    """マイグレーションを全部実行して作るものと、モデル(create_all)が作るものが同じ:テーブル・列・インデックス・UNIQUE・外部キー。"""
    run_migrations(tmp_engine)
    by_models = create_engine(f"sqlite:///{tmp_path}/models.db")
    Base.metadata.create_all(by_models)
    migrated, modeled = _shape(tmp_engine), _shape(by_models)
    by_models.dispose()
    assert set(migrated) == set(modeled) == set(Base.metadata.tables) == BASELINE_TABLES | NEW_TABLES_0002
    for t in modeled:
        assert set(migrated[t]["columns"]) == set(modeled[t]["columns"]) == {c.name for c in Base.metadata.tables[t].columns}, t
        assert migrated[t] == modeled[t], t


def test_baseline_tables_are_frozen():
    """BASELINE_TABLES は 0001 の時点のまま(新しいテーブルを足しても増やさない。起動時の「導入前の DB か」の判定に使うため)。"""
    assert BASELINE_TABLES == {"users", "items", "orders", "offers", "likes"}


def test_models_and_migrations_are_in_sync(tmp_engine):
    """モデルを変えたのにマイグレーションを足し忘れたら、ここで落ちる(autogenerate の差分が空であること)。"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    run_migrations(tmp_engine)
    with tmp_engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == []
