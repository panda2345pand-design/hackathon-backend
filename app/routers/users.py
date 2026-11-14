from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import schemas as S
from app.auth import auth_uid, current_user
from app.db import get_db
from app.models.entities import User
from app.repositories import items as items_repo
from app.repositories import offers as offers_repo
from app.repositories import orders as orders_repo
from app.services import notifications as notifications_service
from app.services import users as users_service

router = APIRouter(tags=["users"])


@router.post("/auth/register", response_model=S.UserOut)
def register(body: S.RegisterIn, uid: str = Depends(auth_uid), db: Session = Depends(get_db)):
    return S.to_user(users_service.register(db, uid, body.name))


@router.get("/me", response_model=S.UserOut)
def me(user: User = Depends(current_user)):
    return S.to_user(user)


@router.get("/me/items")
def my_items(user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = items_repo.by_seller(db, user.id)
    counts = items_repo.like_counts(db, [i.id for i in items])
    return {"items": [S.to_summary(i, counts.get(i.id, 0)) for i in items]}


@router.get("/me/orders")
def my_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"orders": [S.to_order(o) for o in orders_repo.by_buyer(db, user.id)]}


@router.get("/me/offers")
def my_offers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"offers": [S.to_offer(o) for o in offers_repo.by_buyer(db, user.id)]}


@router.get("/me/notifications")
def my_notifications(limit: int = Query(default=20, ge=1, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    notifications, unread = notifications_service.list_for(db, user, limit)
    return {"unread_count": unread, "notifications": [S.to_notification(n) for n in notifications]}


@router.post("/me/notifications/read-all")
def read_all_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"unread_count": notifications_service.read_all(db, user)}
