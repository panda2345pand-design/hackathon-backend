"""クラスタの日本語名(label_ja)と、寄せ集めクラスタを一覧から隠す動きのテスト。"""
import json
from pathlib import Path

import pytest

from app.ml import predictor
from tests.conftest import ITEM

MODEL_DIR = Path(__file__).resolve().parents[1] / "models_ml"
ARTIFACTS = ["price.joblib", "price_metrics.json", "price_aux.json", "cluster.joblib", "cluster_info.json"]
needs_artifacts = pytest.mark.skipif(not all((MODEL_DIR / f).exists() for f in ARTIFACTS), reason="models_ml/ artifacts not built")
HIDDEN = {23, 28, 31}


def _use_dir(monkeypatch, d: Path):
    monkeypatch.setattr(predictor, "ARTIFACT_DIR", d)
    predictor.reset_cache()


@pytest.fixture
def real(monkeypatch):
    _use_dir(monkeypatch, MODEL_DIR)
    yield predictor
    predictor.reset_cache()


@pytest.fixture
def copy_dir(monkeypatch, tmp_path):
    """本物のモデルだけを置いた一時フォルダ(日本語名のファイルは無い。テストごとに入れ替える)。"""
    for f in ARTIFACTS:
        (tmp_path / f).symlink_to(MODEL_DIR / f)
    _use_dir(monkeypatch, tmp_path)
    yield tmp_path
    predictor.reset_cache()


# ---------------------------------------------------------------- 対応表のファイルそのもの
@needs_artifacts
def test_mapping_file_covers_all_clusters():
    rows = json.loads((MODEL_DIR / "cluster_labels_ja.json").read_text(encoding="utf-8"))["clusters"]
    info = json.loads((MODEL_DIR / "cluster_info.json").read_text(encoding="utf-8"))
    assert [r["cluster_id"] for r in rows] == list(range(info["k"])) and info["k"] == 38
    assert {r["cluster_id"]: r["label"] for r in rows} == {c["cluster_id"]: c["label"] for c in info["clusters"]}
    names = [r["label_ja"] for r in rows]
    assert all(isinstance(n, str) and 1 <= len(n) <= 12 for n in names)  # チップに収まる長さ
    assert len(set(names)) == len(names)  # 同じ名前のチップが2つ並ばない
    assert {r["cluster_id"] for r in rows if r["hidden"]} == HIDDEN
    assert all(r.get("reason") for r in rows if r["hidden"])  # 隠す理由を必ず書く


# ---------------------------------------------------------------- 推論モジュール
@needs_artifacts
def test_predictor_japanese_labels(real):
    en, ja = real.cluster_labels(), real.cluster_labels_ja()
    assert set(ja) == set(en) and real.hidden_clusters() == HIDDEN
    assert ja[35] == "ナイキ スニーカー" and en[35] == "Men: nike / air / max"  # 英語のラベルは変えていない
    clusters = real.model_info()["cluster"]["clusters"]
    assert all(c["label_ja"] == ja[c["cluster_id"]] and c["hidden"] == (c["cluster_id"] in HIDDEN) for c in clusters)


@needs_artifacts
def test_missing_or_broken_mapping_falls_back_to_english(copy_dir):
    assert predictor.cluster_labels_ja() == predictor.cluster_labels() and predictor.hidden_clusters() == set()
    (copy_dir / "cluster_labels_ja.json").write_text("not json", encoding="utf-8")
    predictor.reset_cache()
    assert predictor.cluster_labels_ja() == predictor.cluster_labels() and predictor.hidden_clusters() == set()
    assert len(predictor.cluster_labels()) == 38  # 日本語名が読めなくてもクラスタ自体は使える


@needs_artifacts
def test_stale_mapping_row_is_ignored(copy_dir):
    """学習し直して中身が変わったクラスタに、古い日本語名や「隠す」印を付けない。"""
    data = json.loads((MODEL_DIR / "cluster_labels_ja.json").read_text(encoding="utf-8"))
    data["clusters"][23]["label"] = "Something: else / after / retraining"
    (copy_dir / "cluster_labels_ja.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    predictor.reset_cache()
    assert predictor.cluster_labels_ja()[23] == predictor.cluster_labels()[23]
    assert predictor.hidden_clusters() == HIDDEN - {23}
    assert predictor.cluster_labels_ja()[35] == "ナイキ スニーカー"  # ほかの行は生きている


def test_no_model_no_labels(monkeypatch, tmp_path):
    _use_dir(monkeypatch, tmp_path / "nothing_here")
    assert predictor.cluster_labels_ja() == {} and predictor.hidden_clusters() == set()
    predictor.reset_cache()


# ---------------------------------------------------------------- API
@needs_artifacts
def test_api_returns_label_ja_and_hides_junk_clusters(client, users, real):
    shoes = client.post("/items", json={**ITEM, "title": "Nike Air Max 90 shoes size 10"}, headers=users["alice"]).json()
    junk_in = {**ITEM, "title": "Ridgid combo tool set", "category": "Tools", "brand": None}
    junk = client.post("/items", json=junk_in, headers=users["alice"]).json()
    junk2 = client.post("/items", json={**junk_in, "title": "Harry Potter Wizard chess set", "category": "Vintage & collectibles"},
                        headers=users["bob"]).json()
    assert shoes["cluster_id"] == 35 and junk["cluster_id"] == 23 and junk2["cluster_id"] == 23

    # 出品の応答・詳細:今までの cluster_label はそのまま、cluster_label_ja が増える
    assert shoes["cluster_label"] == "Men: nike / air / max" and shoes["cluster_label_ja"] == "ナイキ スニーカー"
    d = client.get(f"/items/{junk['id']}").json()
    assert d["cluster_label"].startswith("Toys & Collectibles: set") and d["cluster_label_ja"] == "セット商品"

    # チップ用の一覧:隠すクラスタは出ない。出るものには label_ja が付く
    chips = client.get("/clusters").json()["clusters"]
    assert [c["id"] for c in chips] == [35]
    assert chips[0] == {"id": 35, "label": "Men: nike / air / max", "label_ja": "ナイキ スニーカー", "count": 1}

    # 隠したクラスタの商品も、「似た商品」と絞り込みは今までどおり
    sim = client.get(f"/items/{junk['id']}/similar").json()
    assert sim["cluster_id"] == 23 and sim["cluster_label_ja"] == "セット商品" and [i["id"] for i in sim["items"]] == [junk2["id"]]
    assert client.get("/items", params={"cluster": 23}).json()["total"] == 2

    info = client.get("/ai/info").json()["ml"]["cluster"]["clusters"]
    assert info[23]["hidden"] is True and info[35]["label_ja"] == "ナイキ スニーカー" and info[35]["hidden"] is False


def test_api_without_cluster_has_null_label_ja(client, users, monkeypatch, tmp_path):
    _use_dir(monkeypatch, tmp_path / "nothing_here")  # モデルが無い環境
    item = client.post("/items", json=ITEM, headers=users["alice"]).json()
    assert item["cluster_id"] is None and item["cluster_label"] is None and item["cluster_label_ja"] is None
    sim = client.get(f"/items/{item['id']}/similar").json()
    assert sim["cluster_label"] is None and sim["cluster_label_ja"] is None
    assert client.get("/clusters").json() == {"clusters": []}
    predictor.reset_cache()
