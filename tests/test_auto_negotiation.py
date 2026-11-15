"""AI 同士の価格交渉のテスト:最低価格の設定と非公開、交渉 → 合意額での購入、回数の上限、せりふ(Gemini / 定型文)。"""
from sqlalchemy import select

from app.db import SessionLocal
from app.models.entities import Negotiation, Offer, Order
from app.services import ai
from tests.conftest import ITEM, auth

# 出品 5,000 円・最低 4,000 円。上限 4,500 円なら 3 往復目(買い手 4,300 / 売り手 4,300)に 4,300 円で合意する
NEGOTIABLE = {**ITEM, "min_price": 4000}


def _create(client, users, **over) -> dict:
    r = client.post("/items", json={**NEGOTIABLE, **over}, headers=users["alice"])
    assert r.status_code == 201, r.text
    return r.json()


def _negotiate(client, who, item_id, max_price):
    return client.post(f"/items/{item_id}/auto-negotiate", json={"max_price": max_price}, headers=who)


def _code(r) -> str:
    return r.json()["detail"]["code"]


# ---------- 最低価格(min_price)の設定 ----------
def test_min_price_validation_on_create(client, users):
    for bad in (0, -1, 5001):
        r = client.post("/items", json={**ITEM, "min_price": bad}, headers=users["alice"])
        assert r.status_code == 422 and _code(r) == "invalid_min_price", bad
    assert client.get("/items").json()["total"] == 0  # 1 件も保存されていない
    assert _create(client, users, min_price=5000)["min_price"] == 5000  # 出品価格と同じ(値下げしない)は可
    assert _create(client, users, min_price=1)["min_price"] == 1
    plain = client.post("/items", json=ITEM, headers=users["alice"]).json()  # 指定しなければ自動交渉なし(今までどおり)
    assert plain["min_price"] is None and plain["auto_negotiation"] is False


def test_min_price_is_visible_only_to_the_seller(client, users):
    item = _create(client, users)
    assert item["min_price"] == 4000 and item["auto_negotiation"] is True  # POST /items の返り値(出品者本人)
    url = f"/items/{item['id']}"
    assert client.get(url, headers=users["alice"]).json()["min_price"] == 4000
    for headers in (users["bob"], {}, {"Authorization": "Bearer nonsense"}, auth("not-registered")):
        r = client.get(url, headers=headers)  # ほかの人・未ログイン・正しくないトークン:読めるが、最低価格は必ず null
        assert r.status_code == 200 and r.json()["min_price"] is None and r.json()["auto_negotiation"] is True, headers
    # 一覧には「自動交渉できるか」だけが出て、金額の項目そのものが無い
    for listing in (client.get("/items").json()["items"], client.get("/me/items", headers=users["alice"]).json()["items"]):
        assert listing[0]["auto_negotiation"] is True and "min_price" not in listing[0]


def test_patch_min_price(client, users, item):
    url = f"/items/{item['id']}/min-price"
    assert client.patch(url, json={"min_price": 4000}).status_code == 401
    r = client.patch(url, json={"min_price": 4000}, headers=users["bob"])
    assert r.status_code == 403 and _code(r) == "not_seller"
    assert client.patch("/items/no-such-id/min-price", json={"min_price": 4000}, headers=users["alice"]).status_code == 404
    for bad in (0, 5001):
        r = client.patch(url, json={"min_price": bad}, headers=users["alice"])
        assert r.status_code == 422 and _code(r) == "invalid_min_price"
    assert client.patch(url, json={}, headers=users["alice"]).status_code == 422  # min_price は必須(null は明示して送る)

    r = client.patch(url, json={"min_price": 4000}, headers=users["alice"])
    assert r.status_code == 200 and r.json()["min_price"] == 4000 and r.json()["auto_negotiation"] is True
    assert r.json()["id"] == item["id"] and r.json()["title"] == ITEM["title"]  # 返り値は商品詳細
    assert client.get(f"/items/{item['id']}", headers=users["bob"]).json()["min_price"] is None
    r = client.patch(url, json={"min_price": None}, headers=users["alice"])  # null = 自動交渉をやめる
    assert r.json()["min_price"] is None and r.json()["auto_negotiation"] is False


def test_auto_negotiation_flag_is_false_after_sold(client, users):
    item = _create(client, users)
    client.post(f"/items/{item['id']}/purchase", headers=users["bob"])
    assert client.get(f"/items/{item['id']}").json()["auto_negotiation"] is False
    assert client.get(f"/items/{item['id']}", headers=users["alice"]).json()["min_price"] == 4000  # 出品者には設定値が見える


# ---------- 交渉 ----------
def test_agreed_then_purchase_at_agreed_price(client, users):
    item = _create(client, users)
    r = _negotiate(client, users["bob"], item["id"], 4500)
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["outcome"] == "agreed" and n["agreed_price"] == 4300 and n["list_price"] == 5000 and n["max_price"] == 4500
    assert n["item_id"] == item["id"] and len(n["id"]) == 36 and n["created_at"] and n["source"] == "fallback" and n["rule"]
    assert [(x["round"], x["buyer_offer"], x["seller_ask"]) for x in n["rounds"]] == [(1, 3800, 4800), (2, 4000, 4500), (3, 4300, 4300)]
    for x in n["rounds"]:  # せりふには、その往復の金額が入っている
        assert f"¥{x['buyer_offer']:,}" in x["buyer_message"] and f"¥{x['seller_ask']:,}" in x["seller_message"]
    assert "min_price" not in n and "floor" not in n  # 売り手の最低価格という項目は、返り値に無い

    # 合意と同時に、承諾済みの交渉(offers)が合意額で作られている
    offers = client.get("/me/offers", headers=users["bob"]).json()["offers"]
    assert len(offers) == 1 and offers[0]["id"] == n["offer_id"]
    assert offers[0]["status"] == "accepted" and offers[0]["amount"] == 4300

    # 既存の購入が、合意額で通る。DB の orders.price も合意額
    order = client.post(f"/items/{item['id']}/purchase", headers=users["bob"])
    assert order.status_code == 201 and order.json()["price"] == 4300
    with SessionLocal() as db:
        assert db.scalar(select(Order.price).where(Order.item_id == item["id"])) == n["agreed_price"] == 4300


def test_other_buyer_still_pays_list_price(client, users):
    item = _create(client, users)
    assert _negotiate(client, users["bob"], item["id"], 4500).json()["outcome"] == "agreed"
    assert client.post(f"/items/{item['id']}/purchase", headers=users["carol"]).json()["price"] == 5000


def test_max_at_list_price_agrees_at_list_price(client, users):
    item = _create(client, users)
    n = _negotiate(client, users["bob"], item["id"], 9_000_000).json()
    assert n["outcome"] == "agreed" and n["agreed_price"] == 5000 and len(n["rounds"]) == 1
    assert client.post(f"/items/{item['id']}/purchase", headers=users["bob"]).json()["price"] == 5000


def test_no_deal_then_retry_and_limit(client, users):
    item = _create(client, users)
    n = _negotiate(client, users["bob"], item["id"], 3000).json()
    assert n["outcome"] == "no_deal" and n["agreed_price"] is None and n["offer_id"] is None and len(n["rounds"]) == 4
    assert "不成立" in n["rule"]
    assert client.get("/me/offers", headers=users["bob"]).json()["offers"] == []  # 不成立では offers を作らない

    item = _create(client, users)  # 回数の上限:1 商品につき 1 人 5 回まで
    for i in range(5):
        assert _negotiate(client, users["bob"], item["id"], 3000 + i).json()["outcome"] == "no_deal"
    r = _negotiate(client, users["bob"], item["id"], 4500)
    assert r.status_code == 429 and _code(r) == "too_many_negotiations"
    assert _negotiate(client, users["carol"], item["id"], 4500).status_code == 200  # ほかの買い手は別に数える
    other = _create(client, users)  # ほかの商品も別に数える
    assert _negotiate(client, users["bob"], other["id"], 4500).status_code == 200


def test_retry_after_no_deal_can_agree(client, users):
    item = _create(client, users)
    assert _negotiate(client, users["bob"], item["id"], 3900).json()["outcome"] == "no_deal"
    assert _negotiate(client, users["bob"], item["id"], 4000).json()["agreed_price"] == 4000


def test_errors(client, users, item):
    assert client.post(f"/items/{item['id']}/auto-negotiate", json={"max_price": 4500}).status_code == 401
    assert _negotiate(client, users["bob"], "no-such-id", 4500).status_code == 404
    assert _negotiate(client, users["bob"], item["id"], 0).status_code == 422  # 1 以上
    r = _negotiate(client, users["bob"], item["id"], 4500)  # item は最低価格なし
    assert r.status_code == 409 and _code(r) == "auto_negotiation_disabled"

    negotiable = _create(client, users)
    r = _negotiate(client, users["alice"], negotiable["id"], 4500)
    assert r.status_code == 403 and _code(r) == "own_item"
    assert _negotiate(client, users["bob"], negotiable["id"], 4500).status_code == 200
    r = _negotiate(client, users["bob"], negotiable["id"], 4800)  # 合意済みなら、やり直せない
    assert r.status_code == 409 and _code(r) == "already_agreed"
    client.post(f"/items/{negotiable['id']}/purchase", headers=users["bob"])
    r = _negotiate(client, users["carol"], negotiable["id"], 4500)
    assert r.status_code == 409 and _code(r) == "sold_out"


def test_already_agreed_also_covers_offer_accepted_by_seller(client, users):
    """出品者が手で承諾した交渉がある買い手も、AI 同士の交渉は始められない(合意額が 2 つにならないように)。"""
    item = _create(client, users)
    oid = client.post(f"/items/{item['id']}/offers", json={"amount": 4200}, headers=users["bob"]).json()["id"]
    client.post(f"/offers/{oid}/accept", headers=users["alice"])
    r = _negotiate(client, users["bob"], item["id"], 4500)
    assert r.status_code == 409 and _code(r) == "already_agreed"


def test_sold_while_waiting_for_gemini(client, users, monkeypatch):
    """せりふを待っている間にほかの人が買ったら、合意を作らずに 409 を返す。"""
    item = _create(client, users)

    def slow_messages(title, rounds, outcome, agreed_price):
        assert client.post(f"/items/{item['id']}/purchase", headers=users["carol"]).status_code == 201
        return ai._fallback_messages(rounds, agreed_price), "fallback"

    monkeypatch.setattr(ai, "negotiation_messages", slow_messages)
    r = _negotiate(client, users["bob"], item["id"], 4500)
    assert r.status_code == 409 and _code(r) == "sold_out"
    with SessionLocal() as db:
        assert db.scalars(select(Negotiation)).all() == [] and db.scalars(select(Offer)).all() == []


# ---------- 記録の一覧と、相手の限度額を見せないこと ----------
def test_negotiation_list_and_privacy(client, users):
    item = _create(client, users)
    url = f"/items/{item['id']}/negotiations"
    assert client.get(url).status_code == 401
    _negotiate(client, users["bob"], item["id"], 3000)
    _negotiate(client, users["bob"], item["id"], 4500)
    _negotiate(client, users["carol"], item["id"], 4100)

    seller = client.get(url, headers=users["alice"]).json()["negotiations"]  # 出品者:全員の記録。買い手の上限は null
    assert len(seller) == 3 and all(n["max_price"] is None for n in seller)
    assert [n["buyer"]["name"] for n in seller] == ["キャロル", "ボブ", "ボブ"]  # 新しい順
    assert set(seller[0]["buyer"]) == {"id", "name"}

    bob = client.get(url, headers=users["bob"]).json()["negotiations"]  # 買い手:自分の記録だけ。自分の上限は見える
    assert [n["max_price"] for n in bob] == [4500, 3000] and [n["outcome"] for n in bob] == ["agreed", "no_deal"]
    assert all(n["buyer"]["name"] == "ボブ" for n in bob)
    assert client.get(url, headers=auth("alice")).status_code == 200
    client.post("/auth/register", json={"name": "デイブ"}, headers=auth("dave"))
    assert client.get(url, headers=auth("dave")).json() == {"negotiations": []}  # 関係ない人には何も出ない


def test_seller_is_notified_only_on_agreement(client, users):
    item = _create(client, users)
    _negotiate(client, users["bob"], item["id"], 3000)
    assert client.get("/me/notifications", headers=users["alice"]).json()["unread_count"] == 0
    _negotiate(client, users["bob"], item["id"], 4500)
    notes = client.get("/me/notifications", headers=users["alice"]).json()["notifications"]
    assert [n["type"] for n in notes] == ["auto_negotiation_agreed"]
    assert "¥4,300" in notes[0]["message"] and "Nike running shoes" in notes[0]["message"] and notes[0]["item_id"] == item["id"]
    assert client.get("/me/notifications", headers=users["bob"]).json()["unread_count"] == 0


# ---------- せりふ(Gemini / 定型文)----------
ROUNDS = [{"round": 1, "buyer_offer": 3900, "seller_ask": 4800}, {"round": 2, "buyer_offer": 4300, "seller_ask": 4200}]


def _gemini_answer(rounds, buyer="{b}円でお願いできますか。", seller="{s}円ならお譲りできます。"):
    return {"rounds": [{"round": r["round"], "buyer_message": buyer.format(b=r["buyer_offer"]),
                        "seller_message": seller.format(s=f"{r['seller_ask']:,}")} for r in rounds]}


def test_gemini_messages_are_used_and_the_prompt_has_no_limits(client, users, monkeypatch):
    prompts = []

    def fake_ask(prompt):
        prompts.append(prompt)
        rounds = [{"round": i + 1, "buyer_offer": b, "seller_ask": s}
                  for i, (b, s) in enumerate([(3800, 4800), (4000, 4500), (4300, 4300)])]
        return _gemini_answer(rounds)

    item = _create(client, users)  # 出品時の禁止物チェックも Gemini を使うので、出品してから差し替える
    monkeypatch.setattr(ai, "_ask_json", fake_ask)
    n = _negotiate(client, users["bob"], item["id"], 4500).json()
    assert n["source"] == "gemini" and n["rounds"][0]["buyer_message"] == "3800円でお願いできますか。"
    assert n["agreed_price"] == 4300  # 金額は規則が決める(Gemini の返事には左右されない)
    assert len(prompts) == 1  # 会話全体で 1 回だけ呼ぶ
    # Gemini には、限度額であることを伝えない(「最低価格」「上限」という言葉を渡さない)
    assert ITEM["title"] in prompts[0] and "最低価格" not in prompts[0] and "上限" not in prompts[0] and "min_price" not in prompts[0]


def test_messages_fall_back_when_gemini_answer_is_wrong(monkeypatch):
    good = _gemini_answer(ROUNDS)
    wrong_answers = [
        None,                                                    # Gemini が使えない
        {},                                                      # 形が違う
        {"rounds": good["rounds"][:1]},                          # 往復の数が違う
        {"rounds": [good["rounds"][0], "x"]},                    # 要素が辞書でない
        {"rounds": [good["rounds"][0], {"buyer_message": "4300円で。"}]},  # せりふが欠けている
        _gemini_answer(ROUNDS, buyer="もう少し安くなりませんか。"),          # 金額が入っていない
        _gemini_answer(ROUNDS, seller="{s}円です。" + "あ" * 80),            # 長すぎる
    ]
    for answer in wrong_answers:
        monkeypatch.setattr(ai, "_ask_json", lambda prompt, a=answer: a)
        messages, source = ai.negotiation_messages("本", ROUNDS, "agreed", 4300)
        assert source == "fallback" and len(messages) == 2, answer
    monkeypatch.setattr(ai, "_ask_json", lambda prompt: _gemini_answer(ROUNDS, buyer="{b}円でいかがですか"))  # 全角の数字も可
    assert ai.negotiation_messages("本", ROUNDS, "agreed", 4300)[1] == "gemini"


def test_fallback_messages_are_short_and_deterministic():
    big = [{"round": r, "buyer_offer": 9_999_998, "seller_ask": 9_999_999} for r in (1, 2, 3, 4)]
    cases = [(big, None), (big, 9_999_997), (ROUNDS, 4300), ([{"round": 1, "buyer_offer": 5000, "seller_ask": 5000}], 5000)]
    for rounds, agreed in cases:
        messages = ai._fallback_messages(rounds, agreed)
        assert messages == ai._fallback_messages(rounds, agreed)
        for r, m in zip(rounds, messages):
            assert len(m["buyer_message"]) <= ai.MESSAGE_CHARS and len(m["seller_message"]) <= ai.MESSAGE_CHARS, m
            assert f"¥{r['buyer_offer']:,}" in m["buyer_message"] and f"¥{r['seller_ask']:,}" in m["seller_message"]
    assert "¥4,300" in ai._fallback_messages(ROUNDS, 4300)[-1]["seller_message"]  # 合意した往復では合意額も言う
    assert "難しい" in ai._fallback_messages(big, None)[-1]["seller_message"]
