"""AI 同士の価格交渉・写真・お知らせ:items に 2 列、新しいテーブル 3 つ

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15

足すもの(消す・変えるものは無い。古いコードが動いたままでも困らない変更だけ):
- 新しいテーブル images / negotiations / notifications
- items.min_price(NULL 可)… AI 同士の価格交渉で売り手が決める最低価格
- items.image_ai_generated(NOT NULL、既定 false)… 画像が AI 生成のイメージかどうか

MySQL はテーブルの変更(DDL)を取り消せない。途中で失敗すると「一部だけ適用済み・版は 0001 のまま」になり、
次の起動でこのファイルが先頭からもう一度実行される。そのとき「もうある」で止まらないように、
1 つずつ「無ければ作る」にしてある(何度実行しても同じ結果になる)。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _offline() -> bool:
    """SQL を出力するだけのモード(alembic upgrade --sql)か。DB につながないので「もうあるか」を調べられない。"""
    return op.get_context().as_sql


def _has_table(table: str) -> bool:
    return not _offline() and sa.inspect(op.get_bind()).has_table(table)


def _has_column(table: str, column: str) -> bool:
    return not _offline() and column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return not _offline() and index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _create_index(name: str, table: str, columns: list[str]) -> None:
    if not _has_index(table, name):
        op.create_index(name, table, columns)


def _mysql_table_options() -> dict:
    """新しいテーブルの文字コードの指定(SQLite では無視される)。0001 と同じ utf8mb4 / InnoDB。

    MySQL は、外部キーの列と参照先の列で文字コードと照合順序(collation)が違うと拒否する(エラー 3780)。
    本番の users / items / offers は Alembic を入れる前に「DB の既定」で作られていて、照合順序が何かはここからは分からない。
    そこで、実際の users.id の文字コードと照合順序を読み、新しいテーブルをそれに合わせる。
    """
    options = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}
    if _offline() or op.get_bind().dialect.name != "mysql":
        return options
    row = op.get_bind().execute(sa.text(
        "SELECT CHARACTER_SET_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'id'")).first()
    if row and row[0] and row[1]:
        options["mysql_charset"], options["mysql_collate"] = row[0], row[1]
    return options


def upgrade() -> None:
    table_options = _mysql_table_options()

    # ---- 1. 新しいテーブル(既存のテーブルに触らないので、先にやる)----
    if not _has_table("images"):
        op.create_table(
            "images",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("owner_id", sa.String(36), nullable=False),
            sa.Column("content_type", sa.String(20), nullable=False),
            # MySQL の BLOB は 64KB まで。2MB の写真を入れるので MEDIUMBLOB(16MB まで)。BLOB / TEXT には既定値を付けられないので付けない
            sa.Column("data", sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"), nullable=False),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            **table_options,
        )
    _create_index("ix_images_owner_id", "images", ["owner_id"])

    if not _has_table("negotiations"):
        op.create_table(
            "negotiations",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("item_id", sa.String(36), nullable=False),
            sa.Column("buyer_id", sa.String(36), nullable=False),
            sa.Column("max_price", sa.Integer(), nullable=False),
            sa.Column("list_price", sa.Integer(), nullable=False),
            sa.Column("outcome", sa.String(10), nullable=False),
            sa.Column("agreed_price", sa.Integer(), nullable=True),
            sa.Column("rounds", sa.Text(), nullable=False),
            sa.Column("offer_id", sa.String(36), nullable=True),
            sa.Column("source", sa.String(10), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
            sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["offer_id"], ["offers.id"]),
            sa.PrimaryKeyConstraint("id"),
            **table_options,
        )
    _create_index("ix_negotiations_item_buyer", "negotiations", ["item_id", "buyer_id"])
    _create_index("ix_negotiations_buyer_id", "negotiations", ["buyer_id"])
    _create_index("ix_negotiations_offer_id", "negotiations", ["offer_id"])

    if not _has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("type", sa.String(30), nullable=False),
            sa.Column("message", sa.String(255), nullable=False),
            sa.Column("item_id", sa.String(36), nullable=True),
            sa.Column("is_read", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
            sa.PrimaryKeyConstraint("id"),
            **table_options,
        )
    _create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    _create_index("ix_notifications_item_id", "notifications", ["item_id"])

    # ---- 2. items に列を足す(すでに商品の行がある)----
    # NULL 可の列:今ある行は NULL(= 自動交渉を受け付けない)になる
    if not _has_column("items", "min_price"):
        op.add_column("items", sa.Column("min_price", sa.Integer(), nullable=True))
    # NOT NULL の列:行があるテーブルに足すので、DB 側の既定値(false)を付ける。今ある行はすべて false になる。
    # 既定値は残しておく。デプロイの途中、この列を知らない古いコードが INSERT しても通るようにするため
    if not _has_column("items", "image_ai_generated"):
        op.add_column("items", sa.Column("image_ai_generated", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    # SQLite で列を消すには、テーブルの作り直し(batch)が要る。MySQL では普通の ALTER TABLE になる
    with op.batch_alter_table("items") as batch:
        batch.drop_column("image_ai_generated")
        batch.drop_column("min_price")
    # 外部キーで参照している側から先に消す(negotiations は offers を参照している)
    for table in ("notifications", "negotiations", "images"):
        op.drop_table(table)
