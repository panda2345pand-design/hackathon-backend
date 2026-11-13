"""価格交渉のユースケース:提案する・受ける・断る。"""
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.models.entities import ACCEPTED, PENDING, REJECTED, SOLD, Offer, User, new_offer
from app.repositories import offers as offers_repo
from app.services import ai
from app.services import items as items_service


def create(db: Session, buyer: User, item_id: str, amount: int, message: str) -> Offer:
    item = items_service.get(db, item_id)
    if item.seller_id == buyer.id:
        raise Forbidden("自分の商品には交渉できません", "own_item")
    if item.status == SOLD:
        raise Conflict("この商品は売り切れです", "sold")
    if amount >= item.price:
        raise Unprocessable("希望額は出品価格より低くしてください", "invalid_amount")
    _, why = ai.acceptance_level(amount, item.price, item.predicted_price)
    offer = offers_repo.add(db, new_offer(item.id, buyer.id, amount, message.strip(), why))
    db.commit()
    return offer


def list_for_item(db: Session, seller: User, item_id: str) -> list[Offer]:
    item = items_service.get(db, item_id)
    if item.seller_id != seller.id:
        raise Forbidden("出品者だけが見られます", "not_seller")
    return offers_repo.by_item(db, item.id)


def decide(db: Session, seller: User, offer_id: str, accept: bool) -> Offer:
    offer = offers_repo.get(db, offer_id)
    if not offer:
        raise NotFound("交渉が見つかりません", "offer_not_found")
    if offer.item.seller_id != seller.id:
        raise Forbidden("出品者だけが返事できます", "not_seller")
    if offer.status != PENDING:
        raise Conflict("この交渉にはもう返事をしています", "already_decided")
    if accept and offer.item.status == SOLD:
        raise Conflict("この商品は売り切れです", "sold")
    offer.status = ACCEPTED if accept else REJECTED
    if accept:
        offers_repo.reject_other_pending(db, offer.item_id, offer.id)
    db.commit()
    db.refresh(offer)
    return offer
