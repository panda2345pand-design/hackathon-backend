"""デモ用データを入れる。seed/seed_items.json(MerRec から取った実在の商品 300件)を、デモ用ユーザーの出品として DB に入れる。

使い方:  python -m scripts.seed            # 商品が1件も無いときだけ入れる
         python -m scripts.seed --reset    # デモ用ユーザーの商品を消して入れ直す(ほかの人のデータは消さない)
価格予測とクラスタは、出品 API と同じ関数(ml_gateway)で付ける。Gemini は呼ばない(300回呼ぶと遅く、費用もかかるため)。
どの商品にも最低価格(min_price)を付ける。審査する人が、どの商品でも AI 同士の価格交渉を試せるようにするため。
"""
import hashlib
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app.db import Base, SessionLocal, engine
from app.models.entities import Item, Like, Negotiation, Notification, Offer, Order, User, new_item, new_user
from app.services import ai, ml_gateway

SEED = Path(__file__).resolve().parent.parent / "seed" / "seed_items.json"
DEMO_USERS = [("demo-sakura", "さくら"), ("demo-ken", "けん"), ("demo-mei", "めい")]
MIN_PRICE_PERCENT_LOW, MIN_PRICE_PERCENT_HIGH = 80, 92  # デモ商品の最低価格は、出品価格の 80〜92%


def demo_min_price(title: str, price: int) -> int:
    """デモ商品の最低価格。商品名から決まる 80〜92% を出品価格に掛け、100 円単位に丸める(100 円以上・出品価格以下)。

    乱数ではなく商品名のハッシュから決める理由:何度入れ直しても、あとから埋め直しても(scripts/backfill_min_price.py)同じ金額になる。
    Python の hash() は起動のたびに値が変わるので使わず、sha256 を使う。商品の id は入れ直すたびに変わるので、商品名を使う。
    """
    spread = MIN_PRICE_PERCENT_HIGH - MIN_PRICE_PERCENT_LOW + 1
    percent = MIN_PRICE_PERCENT_LOW + hashlib.sha256(title.encode("utf-8")).digest()[0] % spread
    rounded = (price * percent + 5000) // 10000 * 100  # 100 円単位に四捨五入(整数だけで計算)
    return min(price, max(100, rounded))


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
            # 商品を参照している行を先に消す。negotiations は offers も参照しているので、offers より前
            for model, col in ((Like, Like.item_id), (Notification, Notification.item_id), (Negotiation, Negotiation.item_id),
                               (Offer, Offer.item_id), (Order, Order.item_id)):
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
            title, price = r["title"][:100], max(300, int(r["price_jpy"]))
            item = new_item(rnd.choice(ids), title, r.get("description") or r["title"], r["category"], r.get("brand"),
                            r["condition"], price, None, est.get("predicted_price"), cl.get("cluster_id"),
                            min_price=demo_min_price(title, price))
            item.created_at -= timedelta(minutes=rnd.randint(0, 60 * 24 * 14))  # 出品日時を過去2週間にばらす
            db.add(item)
            n += 1
        db.commit()
    print(f"入れた商品 {n} 件(規則で外した {skipped} 件)。デモ用ユーザー {len(DEMO_USERS)} 人")
    return n


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
