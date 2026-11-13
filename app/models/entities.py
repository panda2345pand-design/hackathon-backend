"""モデル層:アプリの主役の名詞(User / Item / Order / Offer / Like)。

方針(README・設計書):ID(UUID)と作成日時は、この層の生成関数 new_xxx() だけが作る。
コントローラーやフロントエンドから ID・日時を受け取らない(設計ワークショップ 2026-11-10 の「4番」)。
テーブル定義(SQLAlchemy)も同じクラスに持たせている。純粋なクリーンアーキテクチャならドメインモデルと
ORM を分けるが、個人開発の規模では二重定義のコストが勝つと判断した(聞かれたらそう答える)。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ON_SALE, SOLD = "on_sale", "sold"
PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # DB には UTC を「タイムゾーンなし」で入れる


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    auth_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # Firebase の uid
    name: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seller_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    condition: Mapped[str] = mapped_column(String(40))
    price: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=ON_SALE)
    predicted_price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 教師あり学習(価格予測)の結果
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 教師なし学習(クラスタリング)の結果
    created_at: Mapped[datetime] = mapped_column(DateTime)

    seller: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_items_status_created", "status", "created_at"),
        Index("ix_items_category", "category"),
        Index("ix_items_cluster", "cluster_id"),
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), unique=True)  # 1商品は1回しか売れない(DB でも守る)
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    price: Mapped[int] = mapped_column(Integer)  # 買った時点の金額(あとで商品価格が変わっても動かない)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    item: Mapped[Item] = relationship(lazy="joined")


class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), index=True)
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, default="")
    ai_comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default=PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    item: Mapped[Item] = relationship(lazy="joined")
    buyer: Mapped[User] = relationship(lazy="joined")


class Like(Base):
    __tablename__ = "likes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("user_id", "item_id"),)


# ---------- 生成関数(ID と作成日時はここでだけ作る) ----------
def new_user(auth_uid: str, name: str) -> User:
    return User(id=_new_id(), auth_uid=auth_uid, name=name, created_at=_now())


def new_item(seller_id: str, title: str, description: str, category: str, brand: str | None, condition: str,
             price: int, image_url: str | None, predicted_price: int | None = None, cluster_id: int | None = None) -> Item:
    return Item(id=_new_id(), seller_id=seller_id, title=title, description=description, category=category,
                brand=brand or None, condition=condition, price=price, image_url=image_url or None, status=ON_SALE,
                predicted_price=predicted_price, cluster_id=cluster_id, created_at=_now())


def new_order(item_id: str, buyer_id: str, price: int) -> Order:
    return Order(id=_new_id(), item_id=item_id, buyer_id=buyer_id, price=price, created_at=_now())


def new_offer(item_id: str, buyer_id: str, amount: int, message: str, ai_comment: str) -> Offer:
    return Offer(id=_new_id(), item_id=item_id, buyer_id=buyer_id, amount=amount, message=message,
                 ai_comment=ai_comment, status=PENDING, created_at=_now())


def new_like(user_id: str, item_id: str) -> Like:
    return Like(user_id=user_id, item_id=item_id, created_at=_now())
