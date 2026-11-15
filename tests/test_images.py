"""写真のテスト:アップロード(種類は中身で判定・2MB まで)、配信、AI 生成の印、写真からの下書き。"""
from app.config import settings
from app.db import SessionLocal
from app.models.entities import KIND_AI_GENERATED
from app.repositories import users as users_repo
from app.services import ai
from app.services import images as images_service
from tests.conftest import ITEM

# 中身の先頭(マジックナンバー)だけ本物で、あとは詰め物。種類の判定は先頭しか見ない
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 100


def _upload(client, who, data, filename="photo.jpg", content_type="image/jpeg"):
    return client.post("/images", files={"file": (filename, data, content_type)}, headers=who)


def _code(r) -> str:
    return r.json()["detail"]["code"]


def test_upload_and_get(client, users):
    r = _upload(client, users["alice"], JPEG)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "url", "content_type"} and len(body["id"]) == 36 and body["content_type"] == "image/jpeg"
    assert body["url"] == f"http://testserver/images/{body['id']}"  # PUBLIC_BASE_URL が空なら、リクエストが届いた URL

    got = client.get(f"/images/{body['id']}")  # 認証なしで読める
    assert got.status_code == 200 and got.content == JPEG and got.headers["content-type"] == "image/jpeg"
    assert got.headers["cache-control"] == "public, max-age=31536000, immutable"
    r = client.get("/images/no-such-id")
    assert r.status_code == 404 and _code(r) == "image_not_found"


def test_upload_requires_auth_and_file(client, users):
    assert client.post("/images", files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 401
    assert client.post("/images", headers=users["alice"]).status_code == 422  # file が無い


def test_type_is_decided_by_content_not_by_header(client, users):
    # 中身が PNG / WebP なら、ヘッダーやファイル名が何であっても中身の種類で保存する
    assert _upload(client, users["alice"], PNG, "a.jpg", "image/jpeg").json()["content_type"] == "image/png"
    assert _upload(client, users["alice"], WEBP, "a.bin", "application/octet-stream").json()["content_type"] == "image/webp"
    # ヘッダーが image/jpeg でも、中身が画像でなければ受け付けない
    for bad in (b"<html><script>alert(1)</script></html>", b"GIF89a" + b"\x00" * 50, b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVE", b""):
        r = _upload(client, users["alice"], bad, "photo.jpg", "image/jpeg")
        assert r.status_code == 415 and _code(r) == "unsupported_image", bad


def test_size_limit_is_2mb(client, users):
    exactly = JPEG + b"\x00" * (images_service.MAX_BYTES - len(JPEG))
    r = _upload(client, users["alice"], exactly)
    assert r.status_code == 201
    assert len(client.get(f"/images/{r.json()['id']}").content) == 2 * 1024 * 1024  # 2MB ちょうどは入り、欠けずに戻る
    r = _upload(client, users["alice"], exactly + b"\x00")
    assert r.status_code == 413 and _code(r) == "image_too_large"


def test_url_uses_public_base_url(client, users, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://api.example.com/")
    body = _upload(client, users["alice"], JPEG).json()
    assert body["url"] == f"https://api.example.com/images/{body['id']}"


def _save_ai_generated() -> str:
    """見本データ用の「AI 生成のイメージ」を 1 枚入れる(API からは作れないので、スクリプトと同じ入口を使う)。"""
    with SessionLocal() as db:
        return images_service.save(db, users_repo.get_by_auth_uid(db, "alice"), PNG, KIND_AI_GENERATED).id


def test_image_ai_generated_flag(client, users):
    uploaded = _upload(client, users["alice"], JPEG).json()
    ai_url = f"https://api.example.com/images/{_save_ai_generated()}"

    def create(image_url):
        r = client.post("/items", json={**ITEM, "image_url": image_url}, headers=users["alice"])
        assert r.status_code == 201, r.text
        return r.json()

    assert create(None)["image_ai_generated"] is False                                   # 写真なし
    assert create(uploaded["url"])["image_ai_generated"] is False                        # 利用者の写真
    assert create("https://example.com/photo.jpg")["image_ai_generated"] is False        # 外部の URL
    assert create("https://example.com/images/00000000-0000-0000-0000-000000000000")["image_ai_generated"] is False  # 無い id
    item = create(ai_url)
    assert item["image_ai_generated"] is True and item["image_url"] == ai_url             # AI 生成のイメージ
    assert client.get(f"/items/{item['id']}").json()["image_ai_generated"] is True
    flags = {i["image_url"]: i["image_ai_generated"] for i in client.get("/items").json()["items"]}  # 一覧にも出る
    assert flags[ai_url] is True and flags[uploaded["url"]] is False and flags[None] is False


# ---------- 写真からの下書き(POST /ai/describe-image)----------
def test_describe_image_fallback_without_gemini(client, users):
    image_id = _upload(client, users["alice"], JPEG).json()["id"]
    assert client.post("/ai/describe-image", json={"image_id": image_id}).status_code == 401
    r = client.post("/ai/describe-image", json={"image_id": image_id}, headers=users["bob"])
    assert r.status_code == 200
    assert r.json() == {"title": None, "category": None, "condition": None, "brand": None, "description": None, "source": "fallback"}
    r = client.post("/ai/describe-image", json={"image_id": "no-such-id"}, headers=users["bob"])
    assert r.status_code == 404 and _code(r) == "image_not_found"


def _fake_gemini(monkeypatch, answer):
    """Gemini につながっていることにして、返事を差し替える。渡された内容(写真と指示)を記録して返す。"""
    seen = []
    monkeypatch.setattr(ai, "_gemini", lambda: object())
    monkeypatch.setattr(ai, "_ask_json", lambda contents: seen.append(contents) or answer)
    return seen


def test_describe_image_with_gemini(client, users, monkeypatch):
    image_id = _upload(client, users["alice"], PNG).json()["id"]
    seen = _fake_gemini(monkeypatch, {"title": " ナイキ ランニングシューズ ", "category": "Men", "condition": "Good",
                                      "brand": "Nike", "description": "白いランニングシューズです。"})
    r = client.post("/ai/describe-image", json={"image_id": image_id}, headers=users["bob"]).json()
    assert r == {"title": "ナイキ ランニングシューズ", "category": "Men", "condition": "Good", "brand": "Nike",
                 "description": "白いランニングシューズです。", "source": "gemini"}
    photo, prompt = seen[0]  # 保存してある写真のバイト列と種類を、そのまま Gemini に渡している
    assert photo.inline_data.data == PNG and photo.inline_data.mime_type == "image/png"
    categories = client.get("/categories").json()
    assert all(c in prompt for c in categories["categories"]) and all(c in prompt for c in categories["conditions"])


def test_describe_image_constrains_category_and_condition(monkeypatch):
    cats, conds = ["Men", "Women"], ["New", "Good"]
    _fake_gemini(monkeypatch, {"title": "靴", "category": "メンズ", "condition": "Excellent", "brand": "", "description": 5})
    out = ai.describe_image(PNG, "image/png", cats, conds)  # 選択肢に無い値・空・文字列でない値は null にする
    assert out == {"title": "靴", "category": None, "condition": None, "brand": None, "description": None, "source": "gemini"}
    _fake_gemini(monkeypatch, {"title": "あ" * 300, "category": ["Men"], "condition": None, "brand": "N" * 200, "description": "説明"})
    out = ai.describe_image(PNG, "image/png", cats, conds)  # 長さは出品の入力欄(商品名 100・ブランド 80)に収める
    assert len(out["title"]) == 100 and len(out["brand"]) == 80 and out["category"] is None and out["condition"] is None
    for useless in (None, {}, {"title": "", "description": None}, {"category": "Men"}):  # 使えるものが無ければ fallback
        _fake_gemini(monkeypatch, useless)
        out = ai.describe_image(PNG, "image/png", cats, conds)
        assert out["source"] == "fallback" and out["title"] is None and out["category"] is None, useless


# ---------- いいねされやすさ(モデルは載せていない)----------
def test_attention_is_unavailable(client):
    body = {"title": "Nike shoes", "category": "Men", "condition": "Good", "brand": None, "price": 3000}
    r = client.post("/ai/attention", json=body)  # 認証不要
    assert r.status_code == 200 and r.json() == {"available": False}
    info = client.get("/ai/info").json()["ml"]["attention"]
    assert info["available"] is False and "0.612" in info["reason"] and "ml/LIKE_REPORT.md" in info["reason"]
