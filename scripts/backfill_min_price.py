"""すでに入っているデモ商品に、最低価格(min_price)を埋める。

なぜ要るか:min_price の列を足した時点で、本番の商品はすべて min_price が NULL(= AI 同士の価格交渉ができない)。
デモ商品を入れ直すと、いいね・交渉・注文も消えてしまうので、min_price だけを後から埋める。

- 対象:出品者がデモ用ユーザー(名前が さくら / けん / めい)で、min_price が NULL の商品だけ。
  値が入っている商品(出品者が自分で決めたもの・前に埋めたもの)には触らない → 何度実行しても結果は同じ。
- 金額:scripts/seed.py の demo_min_price() と同じ規則(商品名から決まる 80〜92%、100 円単位)。
- 注意:出品者は名前で選んでいる。同じ名前でふつうに登録した人がいると、その人の商品も対象になる。
  先に dry_run=True で、出品者ごとの件数を見て確かめること。

使い方:  python -m scripts.backfill_min_price --dry-run   # 件数を見るだけ(DATABASE_URL の DB)
         python -m scripts.backfill_min_price             # 実行
ほかのコードからは run(engine) を呼ぶ(接続先を自分で用意できる)。
"""
import sys

from sqlalchemy import Engine, text

from scripts.seed import DEMO_USERS, demo_min_price

DEMO_NAMES = [name for _, name in DEMO_USERS]


def run(engine: Engine, dry_run: bool = False) -> int:
    """min_price を埋めて、変えた行数を返す。dry_run=True なら、何も変えずに「変えるはずの行数」を返す。"""
    names = {f"n{i}": name for i, name in enumerate(DEMO_NAMES)}
    placeholders = ", ".join(f":{key}" for key in names)
    changed, per_seller = 0, {}
    with engine.begin() as conn:  # 1 つのトランザクション。途中で失敗したら、全部が元に戻る
        rows = conn.execute(text(
            "SELECT i.id, i.title, i.price, u.name, u.auth_uid FROM items i JOIN users u ON u.id = i.seller_id "
            f"WHERE i.min_price IS NULL AND u.name IN ({placeholders}) ORDER BY i.id"), names).all()
        for item_id, title, price, name, auth_uid in rows:
            if not dry_run:
                # min_price IS NULL をもう一度条件に入れる:読んでから書くまでの間に出品者が自分で設定していたら、上書きしない
                done = conn.execute(text("UPDATE items SET min_price = :m WHERE id = :id AND min_price IS NULL"),
                                    {"m": demo_min_price(title, price), "id": item_id}).rowcount
                if not done:
                    continue
            changed += 1
            per_seller[f"{name}({auth_uid})"] = per_seller.get(f"{name}({auth_uid})", 0) + 1
    for seller, n in sorted(per_seller.items()):
        print(f"  {seller}: {n} 件")
    print(f"min_price を{'埋める予定の' if dry_run else '埋めた'}商品 {changed} 件(対象の出品者の名前: {' / '.join(DEMO_NAMES)})")
    return changed


if __name__ == "__main__":
    from app.db import engine
    run(engine, dry_run="--dry-run" in sys.argv)
