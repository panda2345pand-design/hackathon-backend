from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.entities import Notification


def add(db: Session, notification: Notification) -> Notification:
    db.add(notification)
    db.flush()
    return notification


def by_user(db: Session, user_id: str, limit: int) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.user_id == user_id)
                           .order_by(Notification.created_at.desc(), Notification.id).limit(limit)).all())


def unread_count(db: Session, user_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Notification)
                     .where(Notification.user_id == user_id, Notification.is_read.is_(False))) or 0


def mark_all_read(db: Session, user_id: str) -> None:
    db.execute(update(Notification).where(Notification.user_id == user_id, Notification.is_read.is_(False)).values(is_read=True))
