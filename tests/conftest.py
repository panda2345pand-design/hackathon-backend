import os
import tempfile

# アプリを import する前に、テスト用の設定にする(DB は一時ファイルの SQLite、認証は dev、Gemini は未接続)
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["AUTH_MODE"] = "dev"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_USE_VERTEX"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def auth(uid: str) -> dict:
    return {"Authorization": f"Bearer dev:{uid}"}


@pytest.fixture()
def users(client):
    """alice(出品者)と bob(購入者)を登録しておく。"""
    for uid, name in (("alice", "アリス"), ("bob", "ボブ"), ("carol", "キャロル")):
        assert client.post("/auth/register", json={"name": name}, headers=auth(uid)).status_code == 200
    return {"alice": auth("alice"), "bob": auth("bob"), "carol": auth("carol")}


ITEM = {"title": "Nike running shoes", "description": "数回履いただけのランニングシューズです。", "category": "Men",
        "brand": "Nike", "condition": "Good", "price": 5000}


@pytest.fixture()
def item(client, users):
    r = client.post("/items", json=ITEM, headers=users["alice"])
    assert r.status_code == 201, r.text
    return r.json()
