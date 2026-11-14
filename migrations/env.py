"""Alembic の実行環境。「どの DB に」「どのテーブル定義(モデル)と比べて」マイグレーションするかを決める。

DB への接続は2通り:
- アプリの起動時(app/migrations.py の run_migrations):アプリが開いた接続をそのまま受け取る。
- コマンドライン(alembic revision / upgrade):app/config.py の DATABASE_URL から自分でつなぐ。
どちらも URL を alembic.ini に通さない(% を含む URL が configparser の補間で壊れるのを避けるため)。
"""
from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  テーブル定義を読み込む(autogenerate がモデルと DB を比べるため)
from app.config import settings
from app.db import Base

target_metadata = Base.metadata


def _configure(**kw) -> None:
    # render_as_batch:SQLite は ALTER TABLE がほとんど使えないので、列の変更を「作り直し」で行わせる(MySQL では普通の ALTER になる)
    # compare_type:autogenerate で列の型の変更も見つける
    context.configure(target_metadata=target_metadata, render_as_batch=True, compare_type=True, **kw)


def run_offline() -> None:
    """DB につながず SQL だけを出力する(alembic upgrade head --sql)。本番に流す前の確認用。"""
    _configure(url=settings.database_url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:  # アプリから呼ばれたとき:渡された接続を使う
        _configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as c:
        _configure(connection=c)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
