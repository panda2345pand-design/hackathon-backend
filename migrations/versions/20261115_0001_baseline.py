"""baseline:Alembic を入れた時点(2026-11-15)のテーブル一式

Revision ID: 0001
Revises:
Create Date: 2026-11-15

app/models/entities.py の User / Item / Order / Offer / Like と同じ内容(テーブル・列・インデックス・制約)。
- 新しい DB では、これを実行してテーブルを作る。
- すでにテーブルがある DB(Alembic を入れる前から動いていた本番)では実行しない。
  app/db_migrate.py が「ここまで適用済み」という印だけを付ける(stamp)。
- このファイルは後から書き換えない。テーブルを変えるときは新しいマイグレーションを足す。
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# MySQL 用の指定(SQLite では無視される)。utf8mb4 = 日本語と絵文字を入れられる文字コード。InnoDB = 外部キーとトランザクションが使えるエンジン
MYSQL = {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("auth_uid", sa.String(128), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        **MYSQL,
    )
    op.create_index("ix_users_auth_uid", "users", ["auth_uid"], unique=True)

    op.create_table(
        "items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("seller_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("brand", sa.String(80), nullable=True),
        sa.Column("condition", sa.String(40), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("predicted_price", sa.Integer(), nullable=True),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        **MYSQL,
    )
    op.create_index("ix_items_seller_id", "items", ["seller_id"])
    op.create_index("ix_items_status_created", "items", ["status", "created_at"])
    op.create_index("ix_items_category", "items", ["category"])
    op.create_index("ix_items_cluster", "items", ["cluster_id"])

    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("buyer_id", sa.String(36), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id"),  # 1商品は1回しか売れない
        **MYSQL,
    )
    op.create_index("ix_orders_buyer_id", "orders", ["buyer_id"])

    op.create_table(
        "offers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("buyer_id", sa.String(36), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("ai_comment", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        **MYSQL,
    )
    op.create_index("ix_offers_item_id", "offers", ["item_id"])
    op.create_index("ix_offers_buyer_id", "offers", ["buyer_id"])

    op.create_table(
        "likes",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"]),
        sa.PrimaryKeyConstraint("user_id", "item_id"),
        sa.UniqueConstraint("user_id", "item_id"),
        **MYSQL,
    )
    op.create_index("ix_likes_item_id", "likes", ["item_id"])


def downgrade() -> None:
    # 外部キーで参照している側から先に消す
    for table in ("likes", "offers", "orders", "items", "users"):
        op.drop_table(table)
