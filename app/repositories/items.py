from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.entities import ON_SALE, SOLD, Item, Like


def add(db: Session, item: Item) -> Item:
    db.add(item)
    db.flush()
    return item


def get(db: Session, item_id: str) -> Item | None:
    return db.get(Item, item_id)


def search(db: Session, q: str | None, category: str | None, cluster: int | None, status: str | None,
           limit: int, offset: int) -> tuple[list[Item], int]:
    cond = []
    if q:
        like = f"%{q}%"
        cond.append(or_(Item.title.ilike(like), Item.description.ilike(like), Item.brand.ilike(like)))
    if category:
        cond.append(Item.category == category)
    if cluster is not None:
        cond.append(Item.cluster_id == cluster)
    if status and status != "all":
        cond.append(Item.status == status)
    total = db.scalar(select(func.count()).select_from(Item).where(*cond)) or 0
    rows = db.scalars(select(Item).where(*cond).order_by(Item.created_at.desc(), Item.id).limit(limit).offset(offset)).all()
    return list(rows), total


def by_seller(db: Session, seller_id: str) -> list[Item]:
    return list(db.scalars(select(Item).where(Item.seller_id == seller_id).order_by(Item.created_at.desc())).all())


def similar(db: Session, item: Item, limit: int) -> list[Item]:
    if item.cluster_id is None:
        return []
    return list(db.scalars(select(Item).where(Item.cluster_id == item.cluster_id, Item.id != item.id, Item.status == ON_SALE)
                           .order_by(Item.created_at.desc()).limit(limit)).all())


def mark_sold_if_on_sale(db: Session, item_id: str) -> bool:
    """「販売中なら売り切れにする」を SQL 1文でやる。2人が同時に買っても、成功するのは1人だけ(rowcount が 1 になるのは片方)。"""
    r = db.execute(update(Item).where(Item.id == item_id, Item.status == ON_SALE).values(status=SOLD))
    return r.rowcount == 1


def cluster_counts(db: Session) -> dict[int, int]:
    rows = db.execute(select(Item.cluster_id, func.count()).where(Item.cluster_id.is_not(None), Item.status == ON_SALE)
                      .group_by(Item.cluster_id)).all()
    return {cid: n for cid, n in rows}


def categories_in_use(db: Session) -> list[str]:
    rows = db.execute(select(Item.category, func.count()).group_by(Item.category).order_by(func.count().desc())).all()
    return [c for c, _ in rows]


def like_counts(db: Session, item_ids: list[str]) -> dict[str, int]:
    if not item_ids:
        return {}
    rows = db.execute(select(Like.item_id, func.count()).where(Like.item_id.in_(item_ids)).group_by(Like.item_id)).all()
    return {i: n for i, n in rows}
