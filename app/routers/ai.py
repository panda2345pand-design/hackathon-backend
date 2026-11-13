from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas as S
from app.auth import current_user
from app.db import get_db
from app.models.entities import User
from app.services import ai, ml_gateway
from app.services import items as items_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/describe")
def describe(body: S.DescribeIn, _: User = Depends(current_user)):
    return ai.describe(body.title, body.category, body.condition, body.brand, body.keywords)


@router.post("/price")
def price(body: S.PriceIn, _: User = Depends(current_user)):
    return ml_gateway.predict_price(body.title, body.category, body.brand, body.condition)


@router.post("/check")
def check(body: S.CheckIn, _: User = Depends(current_user)):
    return ai.check(body.title, body.description, body.category)


@router.post("/negotiate")
def negotiate(body: S.NegotiateIn, _: User = Depends(current_user), db: Session = Depends(get_db)):
    item = items_service.get(db, body.item_id)
    return ai.negotiate(item.title, item.price, item.predicted_price, body.amount)


@router.get("/info")
def info():
    """透明化:どの AI が何で動いているか(フロントの「AI のしくみ」ページ用)。"""
    from app.config import settings
    return {"gemini": {"connected": ai._gemini() is not None, "model": settings.gemini_model}, "ml": ml_gateway.model_info()}
