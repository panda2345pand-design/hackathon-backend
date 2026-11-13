"""認証:Authorization ヘッダーのトークンから「誰か」を決める(コントローラー層の依存関数)。

- AUTH_MODE=firebase(本番):Firebase の ID トークンを、Google の公開鍵で署名検証する。
- AUTH_MODE=dev(ローカル・テスト):「dev:<uid>」をそのまま信じる。本番では使わない。
"""
import logging

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import Unauthorized
from app.models.entities import User
from app.repositories import users as users_repo
from app.services import users as users_service

log = logging.getLogger(__name__)


def _verify_firebase(token: str) -> str:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_firebase_token(token, google_requests.Request(), audience=settings.firebase_project_id)
    except Exception as e:
        log.info("firebase token rejected: %r", e)
        raise Unauthorized("ログインの有効期限が切れたか、トークンが正しくありません")
    uid = (claims or {}).get("user_id") or (claims or {}).get("sub")
    if not uid:
        raise Unauthorized("トークンにユーザー情報がありません")
    return uid


def _uid_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Authorization ヘッダーの形が正しくありません")
    if settings.auth_mode == "dev":
        if not token.startswith("dev:") or len(token) <= 4:
            raise Unauthorized("開発用トークンは dev:<uid> の形です")
        return token[4:]
    return _verify_firebase(token)


def auth_uid(authorization: str | None = Header(default=None)) -> str:
    uid = _uid_from_header(authorization)
    if not uid:
        raise Unauthorized("ログインが必要です")
    return uid


def current_user(uid: str = Depends(auth_uid), db: Session = Depends(get_db)) -> User:
    return users_service.require(db, uid)


def optional_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User | None:
    try:
        uid = _uid_from_header(authorization)
    except Unauthorized:
        return None
    return users_repo.get_by_auth_uid(db, uid) if uid else None
