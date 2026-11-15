"""リクエスト/レスポンスの形(pydantic)。コントローラー層が使う。モデル → JSON の変換(to_xxx)もここ。"""
import json
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.entities import ON_SALE, Item, Negotiation, Notification, Offer, Order, User


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
    # AI 同士の価格交渉の最低価格(任意)。範囲(1 以上 price 以下)は services/items.py で確かめる(エラーコード invalid_min_price を返すため)
    min_price: int | None = None


class MinPriceIn(BaseModel):
    min_price: int | None  # 必須(null は「自動交渉をやめる」)。{} を送っただけで設定が消えないように、既定値は付けない


class AutoNegotiateIn(BaseModel):
    max_price: int = Field(ge=1, le=9_999_999)


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


class DescribeImageIn(BaseModel):
    image_id: str = Field(min_length=1, max_length=36)


class AttentionIn(BaseModel):
    title: str = Field(max_length=100)
    category: str = Field(max_length=80)
    condition: str = Field(max_length=40)
    brand: str | None = Field(default=None, max_length=80)
    price: int


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
    auto_negotiation: bool = False  # 売り手が最低価格を設定していて販売中 = AI 同士の価格交渉ができる(金額は出さない)
    image_ai_generated: bool = False  # 画像が、見本データ用に AI が作ったイメージか


class ItemDetail(ItemSummary):
    description: str
    brand: str | None
    predicted_price: int | None
    cluster_label: str | None
    cluster_label_ja: str | None = None  # 日本語名(手で付けたもの)。無ければ cluster_label と同じ
    seller: UserRef
    liked: bool
    min_price: int | None = None  # 出品者本人が読んだときだけ値が入る。それ以外は必ず null


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


class NegotiationRound(BaseModel):
    round: int
    buyer_offer: int
    buyer_message: str
    seller_ask: int
    seller_message: str


class NegotiationOut(BaseModel):
    id: str
    item_id: str
    outcome: str  # agreed | no_deal
    agreed_price: int | None
    list_price: int
    max_price: int | None  # 買い手本人への返り値にだけ入る
    rounds: list[NegotiationRound]
    offer_id: str | None
    source: str  # せりふの出どころ gemini | fallback
    rule: str
    created_at: datetime
    buyer: UserRef


class ImageOut(BaseModel):
    id: str
    url: str
    content_type: str


class NotificationOut(BaseModel):
    id: str
    type: str
    message: str
    item_id: str | None
    read: bool
    created_at: datetime


def to_user(u: User) -> UserOut:
    return UserOut(id=u.id, name=u.name, created_at=u.created_at)


def to_summary(i: Item, like_count: int = 0) -> ItemSummary:
    return ItemSummary(id=i.id, title=i.title, price=i.price, category=i.category, condition=i.condition, image_url=i.image_url,
                       status=i.status, cluster_id=i.cluster_id, like_count=like_count, created_at=i.created_at,
                       auto_negotiation=i.min_price is not None and i.status == ON_SALE,
                       image_ai_generated=bool(i.image_ai_generated))


def to_detail(i: Item, like_count: int, liked: bool, cluster_label: str | None,
              cluster_label_ja: str | None = None, show_min_price: bool = False) -> ItemDetail:
    """show_min_price は、読んでいるのが出品者本人のときだけ True にする(既定は出さない)。"""
    return ItemDetail(**to_summary(i, like_count).model_dump(), description=i.description, brand=i.brand,
                      predicted_price=i.predicted_price, cluster_label=cluster_label,
                      cluster_label_ja=cluster_label_ja or cluster_label,
                      seller=UserRef(id=i.seller.id, name=i.seller.name), liked=liked,
                      min_price=i.min_price if show_min_price else None)


def to_order(o: Order) -> OrderOut:
    return OrderOut(id=o.id, item_id=o.item_id, item_title=o.item.title, price=o.price, created_at=o.created_at)


def to_offer(o: Offer) -> OfferOut:
    return OfferOut(id=o.id, item_id=o.item_id, item_title=o.item.title, buyer=UserRef(id=o.buyer.id, name=o.buyer.name),
                    amount=o.amount, message=o.message, ai_comment=o.ai_comment, status=o.status, created_at=o.created_at)


def to_negotiation(n: Negotiation, show_max_price: bool, rule: str) -> NegotiationOut:
    """show_max_price は、読んでいるのが買い手本人のときだけ True にする(出品者には上限を見せない)。"""
    return NegotiationOut(id=n.id, item_id=n.item_id, outcome=n.outcome, agreed_price=n.agreed_price, list_price=n.list_price,
                          max_price=n.max_price if show_max_price else None, rounds=json.loads(n.rounds),
                          offer_id=n.offer_id, source=n.source, rule=rule, created_at=n.created_at,
                          buyer=UserRef(id=n.buyer.id, name=n.buyer.name))


def to_notification(n: Notification) -> NotificationOut:
    return NotificationOut(id=n.id, type=n.type, message=n.message, item_id=n.item_id, read=n.is_read, created_at=n.created_at)
