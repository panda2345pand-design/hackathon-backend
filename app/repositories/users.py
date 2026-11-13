from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import User


def get_by_auth_uid(db: Session, auth_uid: str) -> User | None:
    return db.scalar(select(User).where(User.auth_uid == auth_uid))


def add(db: Session, user: User) -> User:
    db.add(user)
    db.flush()
    return user
