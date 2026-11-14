"""「似ている商品」は、同じ AI ジャンルの中で同じカテゴリの商品を先に並べる。"""
from sqlalchemy import update

from app.db import SessionLocal
from app.models.entities import Item
from tests.conftest import ITEM


def test_similar_puts_same_category_first(client, users):
    def create(title, category):
        r = client.post("/items", json={**ITEM, "title": title, "category": category}, headers=users["alice"])
        assert r.status_code == 201, r.text
        return r.json()["id"]

    target = create("Black mountain parka", "Men")
    same = create("Black leather jacket", "Men")
    other_newer = create("Black ink cartridge", "Office")
    with SessionLocal() as db:  # 3つを同じクラスタに入れる(モデルの出力に左右されないように直接そろえる)
        db.execute(update(Item).where(Item.id.in_([target, same, other_newer])).values(cluster_id=32))
        db.commit()

    ids = [i["id"] for i in client.get(f"/items/{target}/similar").json()["items"]]
    assert ids == [same, other_newer]  # 新しい順なら other_newer が先だが、同じカテゴリの same が先に来る
