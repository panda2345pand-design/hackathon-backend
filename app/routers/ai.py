from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import schemas as S
from app.auth import current_user
from app.db import get_db
from app.models.entities import User
from app.services import ai, ml_gateway
from app.services import images as images_service
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


@router.post("/describe-image")
def describe_image(body: S.DescribeImageIn, _: User = Depends(current_user), db: Session = Depends(get_db)):
    image = images_service.get(db, body.image_id)
    return ai.describe_image(image.data, image.content_type, ml_gateway.category_choices(), ml_gateway.condition_choices())


@router.post("/attention")
def attention(_: S.AttentionIn):
    """いいねされやすさ(2本目の教師あり学習)。モデルは精度が基準に届かず載せていないので、いつも「無い」と返す。
    入口だけ残してあるのは、画面がこの返り値を見て欄を出さない、という動きにできるようにするため。"""
    return {"available": False}


@router.get("/info")
def info():
    """透明化:どの AI が何で動いているか(フロントの「AI のしくみ」ページ用)。"""
    from app.config import settings
    ml = {**ml_gateway.model_info(), "attention": dict(ml_gateway.ATTENTION_UNAVAILABLE)}
    return {"gemini": {"connected": ai._gemini() is not None, "model": settings.gemini_model}, "ml": ml}
