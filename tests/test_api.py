"""API の結合テスト:必須機能(登録・認証/出品/購入)と、交渉・いいね・AI の代替動作。"""
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import ITEM, auth


# ---------- 認証 ----------
def test_auth_required(client):
    assert client.get("/me").status_code == 401
    assert client.post("/items", json=ITEM).status_code == 401
    r = client.get("/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized"


def test_register_and_me(client):
    r = client.get("/me", headers=auth("dave"))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_registered"
    r = client.post("/auth/register", json={"name": "デイブ"}, headers=auth("dave"))
    assert r.status_code == 200 and r.json()["name"] == "デイブ"
    uid = r.json()["id"]
    r = client.post("/auth/register", json={"name": "デイブ2"}, headers=auth("dave"))  # 2回目は名前の更新
    assert r.json()["id"] == uid and r.json()["name"] == "デイブ2"
    assert client.get("/me", headers=auth("dave")).json()["id"] == uid


# ---------- 出品・一覧・詳細 ----------
def test_create_and_get_item(client, users, item):
    assert len(item["id"]) == 36 and item["status"] == "on_sale" and item["seller"]["name"] == "アリス"
    r = client.get(f"/items/{item['id']}")
    assert r.status_code == 200 and r.json()["title"] == ITEM["title"] and r.json()["liked"] is False
    assert client.get("/items/no-such-id").status_code == 404


def test_client_cannot_set_id_or_status(client, users):
    r = client.post("/items", json={**ITEM, "id": "hacked", "status": "sold", "created_at": "2000-01-01T00:00:00"}, headers=users["alice"])
    assert r.status_code == 201
    assert r.json()["id"] != "hacked" and r.json()["status"] == "on_sale" and not r.json()["created_at"].startswith("2000")


def test_item_validation(client, users):
    assert client.post("/items", json={**ITEM, "price": 100}, headers=users["alice"]).status_code == 422
    assert client.post("/items", json={**ITEM, "title": ""}, headers=users["alice"]).status_code == 422


def test_prohibited_item_is_blocked(client, users):
    r = client.post("/items", json={**ITEM, "title": "ブランドバッグ スーパーコピー"}, headers=users["alice"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "prohibited"
    assert "コピー" in r.json()["detail"]["message"]
    assert client.get("/items").json()["total"] == 0


def test_list_search_filter(client, users, item):
    client.post("/items", json={**ITEM, "title": "Kids picture book", "category": "Kids", "brand": None}, headers=users["bob"])
    assert client.get("/items").json()["total"] == 2
    assert client.get("/items", params={"q": "nike"}).json()["total"] == 1
    assert client.get("/items", params={"category": "Kids"}).json()["items"][0]["title"] == "Kids picture book"
    assert client.get("/items", params={"limit": 1}).json()["items"].__len__() == 1


# ---------- 購入 ----------
def test_purchase_flow(client, users, item):
    r = client.post(f"/items/{item['id']}/purchase", headers=users["alice"])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "own_item"
    r = client.post(f"/items/{item['id']}/purchase", headers=users["bob"])
    assert r.status_code == 201 and r.json()["price"] == 5000
    assert client.get(f"/items/{item['id']}").json()["status"] == "sold"
    r = client.post(f"/items/{item['id']}/purchase", headers=users["carol"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sold"
    assert client.get("/me/orders", headers=users["bob"]).json()["orders"][0]["item_title"] == ITEM["title"]
    assert client.get("/me/items", headers=users["alice"]).json()["items"][0]["status"] == "sold"
    assert client.get("/items").json()["total"] == 0  # 既定は販売中だけ
    assert client.get("/items", params={"status": "all"}).json()["total"] == 1


def test_concurrent_purchase_only_one_wins(client, users, item):
    def buy(who):
        return client.post(f"/items/{item['id']}/purchase", headers=users[who]).status_code
    with ThreadPoolExecutor(max_workers=2) as ex:
        codes = sorted(ex.map(buy, ["bob", "carol"]))
    assert codes == [201, 409]


# ---------- 価格交渉 ----------
def test_offer_accept_then_purchase_at_offer_price(client, users, item):
    assert client.post(f"/items/{item['id']}/offers", json={"amount": 4000}, headers=users["alice"]).status_code == 403
    assert client.post(f"/items/{item['id']}/offers", json={"amount": 5000}, headers=users["bob"]).status_code == 422
    r = client.post(f"/items/{item['id']}/offers", json={"amount": 4200, "message": "お願いします"}, headers=users["bob"])
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["ai_comment"]
    offer_bob = r.json()["id"]
    offer_carol = client.post(f"/items/{item['id']}/offers", json={"amount": 3000}, headers=users["carol"]).json()["id"]
    assert client.get(f"/items/{item['id']}/offers", headers=users["bob"]).status_code == 403
    assert len(client.get(f"/items/{item['id']}/offers", headers=users["alice"]).json()["offers"]) == 2
    assert client.post(f"/offers/{offer_bob}/accept", headers=users["bob"]).status_code == 403
    assert client.post(f"/offers/{offer_bob}/accept", headers=users["alice"]).json()["status"] == "accepted"
    statuses = {o["id"]: o["status"] for o in client.get(f"/items/{item['id']}/offers", headers=users["alice"]).json()["offers"]}
    assert statuses[offer_carol] == "rejected"  # ほかの保留中の交渉は自動で断る
    assert client.post(f"/offers/{offer_bob}/reject", headers=users["alice"]).status_code == 409
    assert client.post(f"/items/{item['id']}/purchase", headers=users["bob"]).json()["price"] == 4200  # 合意した額で買える
    assert client.get("/me/offers", headers=users["bob"]).json()["offers"][0]["status"] == "accepted"


def test_other_buyer_pays_list_price(client, users, item):
    oid = client.post(f"/items/{item['id']}/offers", json={"amount": 4200}, headers=users["bob"]).json()["id"]
    client.post(f"/offers/{oid}/accept", headers=users["alice"])
    assert client.post(f"/items/{item['id']}/purchase", headers=users["carol"]).json()["price"] == 5000


# ---------- いいね ----------
def test_like_unlike(client, users, item):
    assert client.post(f"/items/{item['id']}/like", headers=users["bob"]).json() == {"like_count": 1, "liked": True}
    assert client.post(f"/items/{item['id']}/like", headers=users["bob"]).json()["like_count"] == 1  # 2回押しても1
    assert client.post(f"/items/{item['id']}/like", headers=users["carol"]).json()["like_count"] == 2
    assert client.get(f"/items/{item['id']}", headers=users["bob"]).json()["liked"] is True
    assert client.get("/items").json()["items"][0]["like_count"] == 2
    assert client.delete(f"/items/{item['id']}/like", headers=users["bob"]).json() == {"like_count": 1, "liked": False}


# ---------- AI(Gemini 未接続のときの代替動作)----------
def test_ai_describe_fallback(client, users):
    r = client.post("/ai/describe", json={"title": "Nike shoes", "category": "Men", "condition": "Good", "brand": "Nike"}, headers=users["bob"])
    assert r.status_code == 200 and r.json()["source"] == "fallback" and "Nike" in r.json()["description"]


def test_ai_check(client, users):
    r = client.post("/ai/check", json={"title": "電子タバコ ニコチン入りリキッド", "description": "", "category": "Other"}, headers=users["bob"])
    assert r.json()["ok"] is False and r.json()["source"] == "rule"
    r = client.post("/ai/check", json={"title": "絵本セット", "description": "読み終わった絵本です", "category": "Kids"}, headers=users["bob"])
    assert r.json()["ok"] is True


def test_ai_negotiate(client, users, item):
    hi = client.post("/ai/negotiate", json={"item_id": item["id"], "amount": 4700}, headers=users["bob"]).json()
    lo = client.post("/ai/negotiate", json={"item_id": item["id"], "amount": 2000}, headers=users["bob"]).json()
    assert hi["acceptance"] == "high" and lo["acceptance"] == "low"
    assert "4,700" in hi["reply"] and hi["source"] == "fallback"


def test_price_estimate_and_meta_endpoints(client, users, item):
    r = client.get(f"/items/{item['id']}/price-estimate")
    assert r.status_code == 200 and "available" in r.json()
    assert client.get("/categories").json()["categories"]
    assert "clusters" in client.get("/clusters").json()
    assert client.get(f"/items/{item['id']}/similar").status_code == 200
    assert "gemini" in client.get("/ai/info").json()
