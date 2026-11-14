"""モデル層:アプリの主役の名詞(User / Item / Order / Offer / Like / Negotiation / Image / Notification)。

方針(README・設計書):ID(UUID)と作成日時は、この層の生成関数 new_xxx() だけが作る。
コントローラーやフロントエンドから ID・日時を受け取らない(設計ワークショップ 2026-11-10 の「4番」)。
テーブル定義(SQLAlchemy)も同じクラスに持たせている。純粋なクリーンアーキテクチャならドメインモデルと
ORM を分けるが、個人開発の規模では二重定義のコストが勝つと判断した(聞かれたらそう答える)。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ON_SALE, SOLD = "on_sale", "sold"
PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"
# 写真の種類。upload = 利用者がアップロードした写真、ai_generated = 見本データ用に AI が作ったイメージ画像
KIND_UPLOAD, KIND_AI_GENERATED = "upload", "ai_generated"
# お知らせの種類
OFFER_RECEIVED, OFFER_ACCEPTED, OFFER_REJECTED = "offer_received", "offer_accepted", "offer_rejected"
ITEM_SOLD, AUTO_NEGOTIATION_AGREED = "item_sold", "auto_negotiation_agreed"


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
    # AI 同士の価格交渉で「ここまでなら下げてよい」金額。NULL = 自動交渉を受け付けない。出品者本人にしか返さない
    min_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # image_url が「AI が作ったイメージ画像」を指しているか。出品時に images.kind を見て決める。
    # 列として持つ理由:一覧を出すたびに images を引かなくて済むようにするため(一覧は items への 1 回の問い合わせのまま)
    # server_default:すでに行があるテーブルに NOT NULL の列を足すので、DB 側の既定値が要る
    image_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

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


class Negotiation(Base):
    """AI 同士の価格交渉 1 回分の記録。"""
    __tablename__ = "negotiations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    buyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    max_price: Mapped[int] = mapped_column(Integer)  # 買い手の上限。買い手本人にしか返さない
    list_price: Mapped[int] = mapped_column(Integer)  # 交渉した時点の出品価格
    outcome: Mapped[str] = mapped_column(String(10))  # agreed | no_deal
    agreed_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 各往復の金額とせりふ(JSON の文字列)。往復だけを検索することは無いので、表を分けずに 1 列に入れる
    rounds: Mapped[str] = mapped_column(Text)
    offer_id: Mapped[str | None] = mapped_column(ForeignKey("offers.id"), nullable=True, index=True)  # 合意したときに作った承諾済みの交渉
    source: Mapped[str] = mapped_column(String(10))  # せりふの出どころ gemini | fallback
    created_at: Mapped[datetime] = mapped_column(DateTime)

    buyer: Mapped[User] = relationship(lazy="joined")

    # 「この商品の記録」「この商品でのこの買い手の記録(回数の上限の確認)」の両方に使う
    __table_args__ = (Index("ix_negotiations_item_buyer", "item_id", "buyer_id"),)


class Image(Base):
    """商品の写真。バイト列そのものを DB に入れる(理由と限界は README の「設計で決めたこと」)。"""
    __tablename__ = "images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content_type: Mapped[str] = mapped_column(String(20))  # image/jpeg | image/png | image/webp
    # MySQL の BLOB は 64KB まで。2MB の写真を入れるので MySQL では MEDIUMBLOB(16MB まで)にする。SQLite の BLOB は大きさの上限が十分大きい
    data: Mapped[bytes] = mapped_column(LargeBinary().with_variant(MEDIUMBLOB(), "mysql"))
    kind: Mapped[str] = mapped_column(String(20))  # upload | ai_generated
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Notification(Base):
    """アプリ内のお知らせ。"""
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))  # お知らせを受け取る人
    type: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(255))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    # 列名を read にしない理由:READ は MySQL の予約語で、SQL を手で書くときに毎回引用符が要るため(JSON では read で返す)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    # 「この人のお知らせを新しい順に」を速くする
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)


# ---------- 生成関数(ID と作成日時はここでだけ作る) ----------
def new_user(auth_uid: str, name: str) -> User:
    return User(id=_new_id(), auth_uid=auth_uid, name=name, created_at=_now())


def new_item(seller_id: str, title: str, description: str, category: str, brand: str | None, condition: str,
             price: int, image_url: str | None, predicted_price: int | None = None, cluster_id: int | None = None,
             min_price: int | None = None, image_ai_generated: bool = False) -> Item:
    return Item(id=_new_id(), seller_id=seller_id, title=title, description=description, category=category,
                brand=brand or None, condition=condition, price=price, image_url=image_url or None, status=ON_SALE,
                predicted_price=predicted_price, cluster_id=cluster_id, created_at=_now(),
                min_price=min_price, image_ai_generated=image_ai_generated)


def new_order(item_id: str, buyer_id: str, price: int) -> Order:
    return Order(id=_new_id(), item_id=item_id, buyer_id=buyer_id, price=price, created_at=_now())


def new_offer(item_id: str, buyer_id: str, amount: int, message: str, ai_comment: str, status: str = PENDING) -> Offer:
    """status を渡すのは、AI 同士の交渉が合意したときだけ(はじめから accepted の行を作る)。"""
    return Offer(id=_new_id(), item_id=item_id, buyer_id=buyer_id, amount=amount, message=message,
                 ai_comment=ai_comment, status=status, created_at=_now())


def new_like(user_id: str, item_id: str) -> Like:
    return Like(user_id=user_id, item_id=item_id, created_at=_now())


def new_negotiation(item_id: str, buyer_id: str, max_price: int, list_price: int, outcome: str, agreed_price: int | None,
                    rounds: str, offer_id: str | None, source: str) -> Negotiation:
    return Negotiation(id=_new_id(), item_id=item_id, buyer_id=buyer_id, max_price=max_price, list_price=list_price,
                       outcome=outcome, agreed_price=agreed_price, rounds=rounds, offer_id=offer_id, source=source,
                       created_at=_now())


def new_image(owner_id: str, content_type: str, data: bytes, kind: str = KIND_UPLOAD) -> Image:
    return Image(id=_new_id(), owner_id=owner_id, content_type=content_type, data=data, kind=kind, created_at=_now())


def new_notification(user_id: str, type: str, message: str, item_id: str | None) -> Notification:
    return Notification(id=_new_id(), user_id=user_id, type=type, message=message, item_id=item_id, is_read=False,
                        created_at=_now())
