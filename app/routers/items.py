from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import schemas as S
from app.auth import current_user, optional_user
from app.db import get_db
from app.models.entities import ON_SALE, User
from app.repositories import items as items_repo
from app.repositories import likes as likes_repo
from app.services import items as items_service
from app.services import ml_gateway
from app.services import offers as offers_service

router = APIRouter(tags=["items"])

# モデルが無いとき用の既定の選択肢(モデルがあれば、モデルが知っているカテゴリ・状態を返す)
DEFAULT_CATEGORIES = ["Women", "Men", "Kids", "Home", "Electronics", "Beauty", "Toys & Collectibles", "Sports & Outdoors", "Other"]
DEFAULT_CONDITIONS = ["New", "Like new", "Good", "Fair", "Poor"]


def _detail(db: Session, item, viewer: User | None) -> S.ItemDetail:
    label = ml_gateway.cluster_labels().get(item.cluster_id) if item.cluster_id is not None else None
    liked = bool(viewer and likes_repo.exists(db, viewer.id, item.id))
    return S.to_detail(item, likes_repo.count(db, item.id), liked, label)


@router.get("/categories")
def categories():
    return {"categories": ml_gateway.categories() or DEFAULT_CATEGORIES, "conditions": ml_gateway.conditions() or DEFAULT_CONDITIONS}


@router.get("/clusters")
def clusters(db: Session = Depends(get_db)):
    labels, counts = ml_gateway.cluster_labels(), items_repo.cluster_counts(db)
    out = [{"id": cid, "label": labels.get(cid, f"グループ {cid}"), "count": n} for cid, n in counts.items()]
    return {"clusters": sorted(out, key=lambda c: -c["count"])}


@router.get("/items")
def list_items(q: str | None = None, category: str | None = None, cluster: int | None = None, status: str = ON_SALE,
               limit: int = Query(default=24, ge=1, le=100), offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    items, total = items_repo.search(db, q, category, cluster, status, limit, offset)
    counts = items_repo.like_counts(db, [i.id for i in items])
    return {"items": [S.to_summary(i, counts.get(i.id, 0)) for i in items], "total": total}


@router.post("/items", response_model=S.ItemDetail, status_code=201)
def create_item(body: S.ItemIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = items_service.create(db, user, body.title, body.description, body.category, body.brand, body.condition, body.price, body.image_url)
    return _detail(db, item, user)


@router.get("/items/{item_id}", response_model=S.ItemDetail)
def get_item(item_id: str, viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    return _detail(db, items_service.get(db, item_id), viewer)


@router.post("/items/{item_id}/purchase", response_model=S.OrderOut, status_code=201)
def purchase(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return S.to_order(items_service.purchase(db, user, item_id))


@router.get("/items/{item_id}/price-estimate")
def price_estimate(item_id: str, db: Session = Depends(get_db)):
    item = items_service.get(db, item_id)
    return ml_gateway.predict_price(item.title, item.category, item.brand, item.condition)


@router.get("/items/{item_id}/similar")
def similar(item_id: str, limit: int = Query(default=8, ge=1, le=24), db: Session = Depends(get_db)):
    item = items_service.get(db, item_id)
    items = items_repo.similar(db, item, limit)
    counts = items_repo.like_counts(db, [i.id for i in items])
    label = ml_gateway.cluster_labels().get(item.cluster_id) if item.cluster_id is not None else None
    return {"cluster_id": item.cluster_id, "cluster_label": label, "items": [S.to_summary(i, counts.get(i.id, 0)) for i in items]}


@router.post("/items/{item_id}/like")
def like(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"like_count": items_service.like(db, user, item_id), "liked": True}


@router.delete("/items/{item_id}/like")
def unlike(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"like_count": items_service.unlike(db, user, item_id), "liked": False}


@router.post("/items/{item_id}/offers", response_model=S.OfferOut, status_code=201)
def create_offer(item_id: str, body: S.OfferIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return S.to_offer(offers_service.create(db, user, item_id, body.amount, body.message))


@router.get("/items/{item_id}/offers")
def item_offers(item_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"offers": [S.to_offer(o) for o in offers_service.list_for_item(db, user, item_id)]}


@router.post("/offers/{offer_id}/accept", response_model=S.OfferOut)
def accept_offer(offer_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return S.to_offer(offers_service.decide(db, user, offer_id, True))


@router.post("/offers/{offer_id}/reject", response_model=S.OfferOut)
def reject_offer(offer_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return S.to_offer(offers_service.decide(db, user, offer_id, False))
