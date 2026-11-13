from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Order


def add(db: Session, order: Order) -> Order:
    db.add(order)
    db.flush()
    return order


def by_buyer(db: Session, buyer_id: str) -> list[Order]:
    return list(db.scalars(select(Order).where(Order.buyer_id == buyer_id).order_by(Order.created_at.desc())).all())
