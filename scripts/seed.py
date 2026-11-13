"""デモ用データを入れる。seed/seed_items.json(MerRec から取った実在の商品 300件)を、デモ用ユーザーの出品として DB に入れる。

使い方:  python -m scripts.seed            # 商品が1件も無いときだけ入れる
         python -m scripts.seed --reset    # デモ用ユーザーの商品を消して入れ直す(ほかの人のデータは消さない)
価格予測とクラスタは、出品 API と同じ関数(ml_gateway)で付ける。Gemini は呼ばない(300回呼ぶと遅く、費用もかかるため)。
"""
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db import Base, SessionLocal, engine
from app.models.entities import Item, Like, Offer, Order, User, new_item, new_user
from app.services import ai, ml_gateway

SEED = Path(__file__).resolve().parent.parent / "seed" / "seed_items.json"
DEMO_USERS = [("demo-sakura", "さくら"), ("demo-ken", "けん"), ("demo-mei", "めい")]


def main(reset: bool = False) -> int:
    Base.metadata.create_all(engine)
    rows = json.loads(SEED.read_text(encoding="utf-8"))
    rnd = random.Random(42)  # 何度やっても同じ並びになるように
    with SessionLocal() as db:
        users = []
        for uid, name in DEMO_USERS:
            u = db.scalar(select(User).where(User.auth_uid == uid))
            if not u:
                u = new_user(uid, name)
                db.add(u)
            users.append(u)
        db.flush()
        ids = [u.id for u in users]
        if reset:
            item_ids = list(db.scalars(select(Item.id).where(Item.seller_id.in_(ids))))
            for model, col in ((Like, Like.item_id), (Offer, Offer.item_id), (Order, Order.item_id)):
                db.execute(delete(model).where(col.in_(item_ids)))
            db.execute(delete(Item).where(Item.id.in_(item_ids)))
        elif db.scalar(select(func.count()).select_from(Item)):
            print("商品がすでにあるので何もしません(入れ直すなら --reset)")
            return 0
        n = skipped = 0
        for r in rows:
            if ai.check_rules(f"{r['title']}\n{r.get('description', '')}"):
                skipped += 1
                continue
            est = ml_gateway.predict_price(r["title"], r["category"], r.get("brand"), r["condition"])
            cl = ml_gateway.assign_cluster(r["title"], r["category"])
            item = new_item(rnd.choice(ids), r["title"][:100], r.get("description") or r["title"], r["category"], r.get("brand"),
                            r["condition"], max(300, int(r["price_jpy"])), None, est.get("predicted_price"), cl.get("cluster_id"))
            item.created_at -= timedelta(minutes=rnd.randint(0, 60 * 24 * 14))  # 出品日時を過去2週間にばらす
            db.add(item)
            n += 1
        db.commit()
    print(f"入れた商品 {n} 件(規則で外した {skipped} 件)。デモ用ユーザー {len(DEMO_USERS)} 人")
    return n


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
