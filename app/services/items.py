"""商品のユースケース:出品する・探す・買う・いいねする。"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.models.entities import ITEM_SOLD, SOLD, Item, Order, User, new_item, new_like, new_order
from app.repositories import items as items_repo
from app.repositories import likes as likes_repo
from app.repositories import offers as offers_repo
from app.repositories import orders as orders_repo
from app.services import ai, ml_gateway
from app.services import notifications as notifications_service


def get(db: Session, item_id: str) -> Item:
    item = items_repo.get(db, item_id)
    if not item:
        raise NotFound("商品が見つかりません", "item_not_found")
    return item


def create(db: Session, seller: User, title: str, description: str, category: str, brand: str | None,
           condition: str, price: int, image_url: str | None) -> Item:
    """出品する。①禁止物チェック → ②価格予測とクラスタ付与(ML)→ ③保存。"""
    verdict = ai.check(title, description, category)
    if not verdict["ok"]:
        raise Unprocessable(verdict["reason"] or "出品できない商品です", "prohibited")
    est = ml_gateway.predict_price(title, category, brand, condition)
    cl = ml_gateway.assign_cluster(title, category)
    item = items_repo.add(db, new_item(seller.id, title.strip(), description.strip(), category, brand, condition, price,
                                       image_url, est.get("predicted_price"), cl.get("cluster_id")))
    db.commit()
    return item


def purchase(db: Session, buyer: User, item_id: str) -> Order:
    """購入する。金額はサーバーが決める(受け入れ済みの交渉があればその額、なければ出品価格)。"""
    item = get(db, item_id)
    if item.seller_id == buyer.id:
        raise Forbidden("自分の商品は購入できません", "own_item")
    if item.status == SOLD:
        raise Conflict("この商品は売り切れです", "sold")
    accepted = offers_repo.accepted_for(db, item.id, buyer.id)
    price = accepted.amount if accepted else item.price
    if not items_repo.mark_sold_if_on_sale(db, item.id):  # 同時に買われた場合、ここで片方だけが通る
        db.rollback()
        raise Conflict("この商品は売り切れです", "sold")
    try:
        order = orders_repo.add(db, new_order(item.id, buyer.id, price))
        notifications_service.notify(db, item.seller_id, ITEM_SOLD, item, price)  # 注文と同じトランザクションで保存する
        db.commit()
    except IntegrityError:  # orders.item_id の UNIQUE が最後の砦
        db.rollback()
        raise Conflict("この商品は売り切れです", "sold")
    db.refresh(order)
    return order


def like(db: Session, user: User, item_id: str) -> int:
    item = get(db, item_id)
    if not likes_repo.exists(db, user.id, item.id):
        likes_repo.add(db, new_like(user.id, item.id))
        db.commit()
    return likes_repo.count(db, item.id)


def unlike(db: Session, user: User, item_id: str) -> int:
    item = get(db, item_id)
    likes_repo.remove(db, user.id, item.id)
    db.commit()
    return likes_repo.count(db, item.id)
