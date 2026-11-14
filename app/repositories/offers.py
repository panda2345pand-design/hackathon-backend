from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import ACCEPTED, PENDING, REJECTED, Offer


def add(db: Session, offer: Offer) -> Offer:
    db.add(offer)
    db.flush()
    return offer


def get(db: Session, offer_id: str) -> Offer | None:
    return db.get(Offer, offer_id)


def by_item(db: Session, item_id: str) -> list[Offer]:
    return list(db.scalars(select(Offer).where(Offer.item_id == item_id).order_by(Offer.created_at.desc())).all())


def by_buyer(db: Session, buyer_id: str) -> list[Offer]:
    return list(db.scalars(select(Offer).where(Offer.buyer_id == buyer_id).order_by(Offer.created_at.desc())).all())


def accepted_for(db: Session, item_id: str, buyer_id: str) -> Offer | None:
    return db.scalar(select(Offer).where(Offer.item_id == item_id, Offer.buyer_id == buyer_id, Offer.status == ACCEPTED)
                     .order_by(Offer.created_at.desc()))


def other_pending(db: Session, item_id: str, keep_id: str) -> list[Offer]:
    """同じ商品の、keep_id 以外の保留中の交渉(reject_other_pending が断る対象)。"""
    return list(db.scalars(select(Offer).where(Offer.item_id == item_id, Offer.id != keep_id, Offer.status == PENDING)).all())


def reject_other_pending(db: Session, item_id: str, keep_id: str) -> None:
    db.execute(update(Offer).where(Offer.item_id == item_id, Offer.id != keep_id, Offer.status == PENDING).values(status=REJECTED))
