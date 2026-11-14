"""リクエスト/レスポンスの形(pydantic)。コントローラー層が使う。モデル → JSON の変換(to_xxx)もここ。"""
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entities import Item, Offer, Order, User


# ---------- 入力 ----------
class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class ItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=3000)
    category: str = Field(min_length=1, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    condition: str = Field(min_length=1, max_length=40)
    price: int = Field(ge=300, le=9_999_999)  # メルカリと同じ下限 300 円
    image_url: str | None = Field(default=None, max_length=2000)


class OfferIn(BaseModel):
    amount: int = Field(ge=300, le=9_999_999)
    message: str = Field(default="", max_length=500)


class DescribeIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    condition: str = Field(min_length=1, max_length=40)
    brand: str | None = Field(default=None, max_length=80)
    keywords: str | None = Field(default=None, max_length=300)


class PriceIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=80)
    brand: str | None = Field(default=None, max_length=80)
    condition: str = Field(min_length=1, max_length=40)


class CheckIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=3000)
    category: str = Field(default="", max_length=80)


class NegotiateIn(BaseModel):
    item_id: str
    amount: int = Field(ge=300, le=9_999_999)


# ---------- 出力 ----------
class UserOut(BaseModel):
    id: str
    name: str
    created_at: datetime


class UserRef(BaseModel):
    id: str
    name: str


class ItemSummary(BaseModel):
    id: str
    title: str
    price: int
    category: str
    condition: str
    image_url: str | None
    status: str
    cluster_id: int | None
    like_count: int
    created_at: datetime


class ItemDetail(ItemSummary):
    description: str
    brand: str | None
    predicted_price: int | None
    cluster_label: str | None
    cluster_label_ja: str | None = None  # 日本語名(手で付けたもの)。無ければ cluster_label と同じ
    seller: UserRef
    liked: bool


class OrderOut(BaseModel):
    id: str
    item_id: str
    item_title: str
    price: int
    created_at: datetime


class OfferOut(BaseModel):
    id: str
    item_id: str
    item_title: str
    buyer: UserRef
    amount: int
    message: str
    ai_comment: str
    status: str
    created_at: datetime


def to_user(u: User) -> UserOut:
    return UserOut(id=u.id, name=u.name, created_at=u.created_at)


def to_summary(i: Item, like_count: int = 0) -> ItemSummary:
    return ItemSummary(id=i.id, title=i.title, price=i.price, category=i.category, condition=i.condition, image_url=i.image_url,
                       status=i.status, cluster_id=i.cluster_id, like_count=like_count, created_at=i.created_at)


def to_detail(i: Item, like_count: int, liked: bool, cluster_label: str | None,
              cluster_label_ja: str | None = None) -> ItemDetail:
    return ItemDetail(**to_summary(i, like_count).model_dump(), description=i.description, brand=i.brand,
                      predicted_price=i.predicted_price, cluster_label=cluster_label,
                      cluster_label_ja=cluster_label_ja or cluster_label,
                      seller=UserRef(id=i.seller.id, name=i.seller.name), liked=liked)


def to_order(o: Order) -> OrderOut:
    return OrderOut(id=o.id, item_id=o.item_id, item_title=o.item.title, price=o.price, created_at=o.created_at)


def to_offer(o: Offer) -> OfferOut:
    return OfferOut(id=o.id, item_id=o.item_id, item_title=o.item.title, buyer=UserRef(id=o.buyer.id, name=o.buyer.name),
                    amount=o.amount, message=o.message, ai_comment=o.ai_comment, status=o.status, created_at=o.created_at)
