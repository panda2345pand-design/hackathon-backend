"""デモ商品の最低価格(min_price)のテスト:決め方の規則と、あとから埋めるスクリプト。"""
import json

from sqlalchemy import text

from app.db import engine
from scripts import backfill_min_price, seed
from scripts.seed import demo_min_price
from tests.conftest import ITEM, auth


def test_demo_min_price_rule():
    rows = json.loads(seed.SEED.read_text(encoding="utf-8"))
    percents = set()
    for r in rows:  # 実際のデモ商品 300 件すべてで確かめる
        title, price = r["title"][:100], max(300, int(r["price_jpy"]))
        m = demo_min_price(title, price)
        assert 100 <= m <= price and m % 100 == 0, r
        assert price * 0.80 - 50 <= m <= price * 0.92 + 50, r  # 80〜92%(100 円単位に丸めた分のずれまで)
        assert m == demo_min_price(title, price)  # 同じ商品なら、いつも同じ金額
        percents.add(round(m / price, 2))
    assert len(percents) > 5  # 商品によって割合が違う
    assert demo_min_price("x", 300) in (200, 300) and demo_min_price("x", 100) == 100


def test_seed_items_are_negotiable(client):
    """デモデータの商品は、どれも AI 同士の価格交渉ができる(一覧と詳細で auto_negotiation が true)。"""
    assert seed.main() > 250
    page = client.get("/items", params={"limit": 100}).json()
    assert page["total"] > 250 and all(i["auto_negotiation"] for i in page["items"])
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM items WHERE min_price IS NULL OR min_price > price OR min_price < 100")).scalar() == 0
    # 買い手として、1 件を定価より少し安い上限で交渉 → 合意できる
    client.post("/auth/register", json={"name": "ボブ"}, headers=auth("bob"))
    item = page["items"][0]
    n = client.post(f"/items/{item['id']}/auto-negotiate", json={"max_price": item["price"] - 100}, headers=auth("bob")).json()
    assert n["outcome"] == "agreed" and n["agreed_price"] <= item["price"] - 100
    assert seed.main(reset=True) > 250  # 交渉の記録とお知らせがあっても、入れ直せる


def test_backfill_min_price(client, users, capsys):
    seed.main()
    with engine.begin() as c:  # 列を足した直後の本番と同じ状態にする(全商品 min_price が NULL)
        c.execute(text("UPDATE items SET min_price = NULL"))
        total = c.execute(text("SELECT COUNT(*) FROM items")).scalar()
        kept = c.execute(text("SELECT id FROM items ORDER BY id LIMIT 1")).scalar()
        c.execute(text("UPDATE items SET min_price = 123 WHERE id = :id"), {"id": kept})  # 出品者が自分で決めた値
    other = client.post("/items", json=ITEM, headers=users["alice"]).json()  # デモ用ユーザーではない人の商品

    assert backfill_min_price.run(engine, dry_run=True) == total - 1  # 件数を見るだけ。何も変えない
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM items WHERE min_price IS NOT NULL")).scalar() == 1

    assert backfill_min_price.run(engine) == total - 1
    assert f"{total - 1} 件" in capsys.readouterr().out  # 変えた件数を表示する
    with engine.connect() as c:
        rows = c.execute(text("SELECT id, title, price, min_price FROM items")).all()
    for item_id, title, price, min_price in rows:
        if item_id == kept:
            assert min_price == 123  # すでに値がある商品は変えない
        elif item_id == other["id"]:
            assert min_price is None  # デモ用ユーザー以外の商品は変えない
        else:
            assert min_price == demo_min_price(title, price)  # seed と同じ規則
    assert backfill_min_price.run(engine) == 0  # 2 回目は何も変えない
