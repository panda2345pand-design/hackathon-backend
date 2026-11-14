"""お知らせ(アプリ内)のテスト:どの出来事で、だれに、どんな文が届くか。"""
from app.services.notifications import message_for, short_title


def _notes(client, who) -> dict:
    r = client.get("/me/notifications", headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def test_message_format():
    assert message_for("offer_received", "Nike running shoes", 5800) == "「Nike running shoes」に ¥5,800 の価格交渉が届きました"
    assert "¥1,234,567" in message_for("item_sold", "車", 1234567)
    long_title = "あ" * 100  # 商品名は最大 100 字。お知らせでは 30 字で切る(列は 255 字まで)
    assert short_title(long_title) == "あ" * 30 + "…"
    for type in ("offer_received", "offer_accepted", "offer_rejected", "item_sold", "auto_negotiation_agreed"):
        assert len(message_for(type, long_title, 9_999_999)) <= 255


def test_auth_required(client):
    assert client.get("/me/notifications").status_code == 401
    assert client.post("/me/notifications/read-all").status_code == 401


def test_offer_received_accepted_rejected(client, users, item):
    assert _notes(client, users["alice"]) == {"unread_count": 0, "notifications": []}
    bob_offer = client.post(f"/items/{item['id']}/offers", json={"amount": 4200}, headers=users["bob"]).json()["id"]
    client.post(f"/items/{item['id']}/offers", json={"amount": 3000}, headers=users["carol"])

    alice = _notes(client, users["alice"])  # 出品者に 2 件(新しい順)
    assert alice["unread_count"] == 2 and [n["type"] for n in alice["notifications"]] == ["offer_received"] * 2
    n = alice["notifications"][0]
    assert n["message"] == "「Nike running shoes」に ¥3,000 の価格交渉が届きました"
    assert n["item_id"] == item["id"] and n["read"] is False and len(n["id"]) == 36 and n["created_at"]
    assert _notes(client, users["bob"])["unread_count"] == 0  # 送った本人には届かない

    client.post(f"/offers/{bob_offer}/accept", headers=users["alice"])
    bob = _notes(client, users["bob"])["notifications"]
    assert [n["type"] for n in bob] == ["offer_accepted"] and "¥4,200" in bob[0]["message"] and "承諾" in bob[0]["message"]
    carol = _notes(client, users["carol"])["notifications"]  # 自動で断られた人にも届く
    assert [n["type"] for n in carol] == ["offer_rejected"] and "¥3,000" in carol[0]["message"]


def test_offer_rejected_by_seller(client, users, item):
    oid = client.post(f"/items/{item['id']}/offers", json={"amount": 4200}, headers=users["bob"]).json()["id"]
    client.post(f"/offers/{oid}/reject", headers=users["alice"])
    bob = _notes(client, users["bob"])["notifications"]
    assert [n["type"] for n in bob] == ["offer_rejected"] and "¥4,200" in bob[0]["message"]


def test_item_sold(client, users, item):
    client.post(f"/items/{item['id']}/purchase", headers=users["bob"])
    alice = _notes(client, users["alice"])["notifications"]
    assert [n["type"] for n in alice] == ["item_sold"]
    assert alice[0]["message"] == "「Nike running shoes」が ¥5,000 で売れました"
    # 買えなかった購入(売り切れ)ではお知らせを作らない
    assert client.post(f"/items/{item['id']}/purchase", headers=users["carol"]).status_code == 409
    assert len(_notes(client, users["alice"])["notifications"]) == 1


def test_failed_event_creates_no_notification(client, users, item):
    """出来事が成立しなかったとき(出品価格以上の交渉は 422)は、お知らせも作られない。"""
    assert client.post(f"/items/{item['id']}/offers", json={"amount": 5000}, headers=users["bob"]).status_code == 422
    assert _notes(client, users["alice"])["unread_count"] == 0


def test_read_all_and_limit(client, users, item):
    for amount in (4000, 4100, 4200):
        client.post(f"/items/{item['id']}/offers", json={"amount": amount}, headers=users["bob"])
    page = client.get("/me/notifications", params={"limit": 2}, headers=users["alice"]).json()
    assert len(page["notifications"]) == 2 and page["unread_count"] == 3  # 未読の件数は全体の数
    assert "¥4,200" in page["notifications"][0]["message"]  # 新しい順
    assert client.get("/me/notifications", params={"limit": 0}, headers=users["alice"]).status_code == 422

    assert client.post("/me/notifications/read-all", headers=users["alice"]).json() == {"unread_count": 0}
    after = _notes(client, users["alice"])
    assert after["unread_count"] == 0 and all(n["read"] for n in after["notifications"]) and len(after["notifications"]) == 3
    # 既読にしたのは自分の分だけ
    oid = client.post(f"/items/{item['id']}/offers", json={"amount": 4300}, headers=users["carol"]).json()["id"]
    client.post(f"/offers/{oid}/reject", headers=users["alice"])
    client.post("/me/notifications/read-all", headers=users["alice"])
    assert _notes(client, users["carol"])["unread_count"] == 1
