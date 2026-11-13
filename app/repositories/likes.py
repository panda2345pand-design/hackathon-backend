from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import Like


def exists(db: Session, user_id: str, item_id: str) -> bool:
    return db.get(Like, (user_id, item_id)) is not None


def add(db: Session, like: Like) -> None:
    db.add(like)
    db.flush()


def remove(db: Session, user_id: str, item_id: str) -> None:
    db.execute(delete(Like).where(Like.user_id == user_id, Like.item_id == item_id))


def count(db: Session, item_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Like).where(Like.item_id == item_id)) or 0
