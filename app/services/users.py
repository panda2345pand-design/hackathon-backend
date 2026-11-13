from sqlalchemy.orm import Session

from app.errors import NotFound, Unprocessable
from app.models.entities import User, new_user
from app.repositories import users as users_repo


def register(db: Session, auth_uid: str, name: str) -> User:
    name = name.strip()
    if not name:
        raise Unprocessable("名前を入力してください", "invalid_name")
    user = users_repo.get_by_auth_uid(db, auth_uid)
    if user:
        user.name = name
    else:
        user = users_repo.add(db, new_user(auth_uid, name))
    db.commit()
    return user


def require(db: Session, auth_uid: str) -> User:
    user = users_repo.get_by_auth_uid(db, auth_uid)
    if not user:
        raise NotFound("ユーザー登録がまだです", "not_registered")
    return user
