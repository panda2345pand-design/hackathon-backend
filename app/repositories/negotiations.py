from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Negotiation


def add(db: Session, negotiation: Negotiation) -> Negotiation:
    db.add(negotiation)
    db.flush()
    return negotiation


def count_for(db: Session, item_id: str, buyer_id: str) -> int:
    """この商品で、この買い手が AI 同士の交渉をした回数(回数の上限の確認用)。"""
    return db.scalar(select(func.count()).select_from(Negotiation)
                     .where(Negotiation.item_id == item_id, Negotiation.buyer_id == buyer_id)) or 0


def by_item(db: Session, item_id: str, buyer_id: str | None = None) -> list[Negotiation]:
    """この商品の記録(新しい順)。buyer_id を渡すと、その買い手の分だけ。"""
    cond = [Negotiation.item_id == item_id]
    if buyer_id:
        cond.append(Negotiation.buyer_id == buyer_id)
    return list(db.scalars(select(Negotiation).where(*cond).order_by(Negotiation.created_at.desc(), Negotiation.id)).all())
