"""app/ml/predictor.py のテスト(アーティファクトあり/なしの両方)。"""
from pathlib import Path

import pytest

from app.ml import predictor

MODEL_DIR = Path(__file__).resolve().parents[1] / "models_ml"
HAS_ARTIFACTS = all((MODEL_DIR / f).exists() for f in
                    ["price.joblib", "price_metrics.json", "price_aux.json", "cluster.joblib", "cluster_info.json"])
needs_artifacts = pytest.mark.skipif(not HAS_ARTIFACTS, reason="models_ml/ artifacts not built")

PRICE_KEYS = {"available", "predicted_price", "low", "high", "currency", "basis", "model"}
MODEL_KEYS = {"name", "mae", "r2", "trained_rows"}


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(predictor, "ARTIFACT_DIR", MODEL_DIR)
    predictor.reset_cache()
    yield predictor
    predictor.reset_cache()


@pytest.fixture
def missing(monkeypatch, tmp_path):
    monkeypatch.setattr(predictor, "ARTIFACT_DIR", tmp_path / "nothing_here")
    predictor.reset_cache()
    yield predictor
    predictor.reset_cache()


@pytest.fixture
def broken(monkeypatch, tmp_path):
    """ファイルはあるが中身が壊れている場合。"""
    for f in ["price.joblib", "cluster.joblib", "price_metrics.json", "price_aux.json", "cluster_info.json"]:
        (tmp_path / f).write_bytes(b"not a real artifact")
    monkeypatch.setattr(predictor, "ARTIFACT_DIR", tmp_path)
    predictor.reset_cache()
    yield predictor
    predictor.reset_cache()


# ---------------------------------------------------------------- アーティファクトなし
@pytest.mark.parametrize("fx", ["missing", "broken"])
def test_unavailable_forms(fx, request):
    p = request.getfixturevalue(fx)
    r = p.predict_price("Nintendo Switch console", "Electronics", "Nintendo", "Good")
    assert set(r) == PRICE_KEYS and set(r["model"]) == MODEL_KEYS
    assert r["available"] is False
    assert r["predicted_price"] is None and r["low"] is None and r["high"] is None
    assert r["currency"] == "JPY" and r["basis"] == []

    assert p.assign_cluster("Nintendo Switch console", "Electronics") == {
        "available": False, "cluster_id": None, "cluster_label": None}
    assert p.cluster_labels() == {}
    assert p.categories() == p.DEFAULT_CATEGORIES
    assert p.conditions() == ["New", "Like new", "Good", "Fair", "Poor"]
    info = p.model_info()
    assert info["price"]["available"] is False and info["cluster"]["available"] is False


# ---------------------------------------------------------------- アーティファクトあり
CASES = [
    ("Nintendo Switch OLED console with dock", "Electronics", "Nintendo", "Like new"),
    ("Lululemon Align leggings size 6 black", "Women", "lululemon athletica", "Good"),
    ("Funko Pop Marvel Spider-Man", "Toys & Collectibles", "Funko", "New"),
    ("vintage glass vase", "Vintage & collectibles", None, "Fair"),
    ("", "Books", None, "Poor"),
    ("日本語だけのタイトル", "Home", "無名ブランド", "Good"),
    ("something", "Unknown category", "", "Unknown condition"),
]


@needs_artifacts
@pytest.mark.parametrize("title,category,brand,condition", CASES)
def test_predict_price_sane(real, title, category, brand, condition):
    r = real.predict_price(title, category, brand, condition)
    assert set(r) == PRICE_KEYS and set(r["model"]) == MODEL_KEYS
    assert r["available"] is True and r["currency"] == "JPY"
    for k in ("predicted_price", "low", "high"):
        assert isinstance(r[k], int) and r[k] > 0 and r[k] % 100 == 0
    assert r["low"] <= r["predicted_price"] <= r["high"]
    assert 2 <= len(r["basis"]) <= 4
    for b in r["basis"]:
        assert set(b) == {"feature", "value", "effect"}
        assert all(isinstance(v, str) for v in b.values()) and b["effect"]
    m = r["model"]
    assert isinstance(m["name"], str) and m["mae"] > 0 and 0 < m["r2"] < 1 and m["trained_rows"] > 100_000


@needs_artifacts
def test_predict_price_reacts_to_inputs(real):
    console = real.predict_price("Nintendo Switch OLED console", "Electronics", "Nintendo", "New")
    sticker = real.predict_price("cute sticker", "Other", None, "New")
    assert console["predicted_price"] > sticker["predicted_price"]
    # 大文字小文字・日本語の状態名でも同じ結果
    a = real.predict_price("Nike Air Max 90", "men", "NIKE", "未使用に近い")
    b = real.predict_price("Nike Air Max 90", "Men", "Nike", "Like new")
    assert a["predicted_price"] == b["predicted_price"]


@needs_artifacts
def test_model_mae_is_jpy(real):
    info = real.model_info()
    r = real.predict_price("Nike Air Max 90", "Men", "Nike", "Good")
    assert r["model"]["mae"] == pytest.approx(info["price"]["test"]["mae_usd"] * real.USD_JPY, abs=0.1)
    # モデルはベースライン(カテゴリ別中央値)より test の MAE が小さい
    assert info["price"]["test"]["mae_usd"] < info["price"]["baseline_test"]["mae_usd"]


@needs_artifacts
def test_clusters(real):
    labels = real.cluster_labels()
    info = real.model_info()["cluster"]
    assert len(labels) == info["k"] and set(labels) == set(range(info["k"]))
    assert all(isinstance(v, str) and v for v in labels.values())
    r = real.assign_cluster("Nintendo Switch OLED console", "Electronics")
    assert r["available"] is True and r["cluster_id"] in labels
    assert r["cluster_label"] == labels[r["cluster_id"]]
    # 同じ入力は同じクラスタ
    assert real.assign_cluster("Nintendo Switch OLED console", "Electronics") == r
    # 学習済みの語が 1 つもない入力は割り当てない
    assert real.assign_cluster("日本語だけ", "") == {"available": False, "cluster_id": None, "cluster_label": None}


@needs_artifacts
def test_categories_and_conditions(real):
    cats = real.categories()
    assert len(cats) == len(set(cats)) >= 10 and cats[0] == "Women"
    assert set(cats) == set(real.CATEGORY_LABELS_JA)
    assert real.conditions() == ["New", "Like new", "Good", "Fair", "Poor"]
    assert set(real.conditions()) == set(real.CONDITION_LABELS_JA)


@needs_artifacts
def test_seed_items():
    import json

    path = Path(__file__).resolve().parents[1] / "seed" / "seed_items.json"
    if not path.exists():
        pytest.skip("seed not built")
    items = json.loads(path.read_text(encoding="utf-8"))
    assert len(items) == 300 and len({i["title"] for i in items}) == 300
    for i in items:
        assert {"title", "category", "brand", "condition", "price_jpy", "description"} <= set(i)
        assert i["category"] in predictor.DEFAULT_CATEGORIES and i["condition"] in predictor.CONDITIONS
        assert isinstance(i["price_jpy"], int) and i["price_jpy"] > 0
