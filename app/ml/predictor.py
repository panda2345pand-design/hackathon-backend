"""学習済みモデルの推論(価格予測・クラスタ割り当て)。

- アーティファクト(models_ml/)は最初の呼び出しで 1 回だけ読み込む。
- ファイルが無い/読めない場合は例外を出さず、"available": False の形を返す。
- 実行時に必要なのは numpy / scikit-learn / joblib だけ(pandas は不要)。
- モデルは Mercari US(MerRec, 2023 年 5 月, USD, 英語タイトル)で学習している。
  円への換算は固定レート USD_JPY を掛けるだけで、日本の相場を学習したものではない。
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

USD_JPY = 150          # 固定の換算レート(1 USD = 150 円)。実勢レートではない
ROUND_JPY = 100        # 100 円単位に丸める

ARTIFACT_DIR = Path(os.environ.get("ML_ARTIFACT_DIR") or Path(__file__).resolve().parents[2] / "models_ml")

# 状態(良い → 悪い)。MerRec の item_condition_id 1..5 の順
CONDITIONS = ["New", "Like new", "Good", "Fair", "Poor"]
# アーティファクトが無いときのフォールバック(学習データでの件数順)
DEFAULT_CATEGORIES = [
    "Women", "Toys & Collectibles", "Men", "Kids", "Electronics", "Home", "Beauty",
    "Vintage & collectibles", "Other", "Books", "Sports & outdoors", "Handmade",
    "Arts & Crafts", "Garden & Outdoor", "Office", "Pet Supplies", "Tools",
]
# 画面表示用の日本語訳(データに含まれるものではなく、こちらで付けた訳)
CONDITION_LABELS_JA = {
    "New": "新品", "Like new": "未使用に近い", "Good": "良い", "Fair": "やや傷あり", "Poor": "悪い",
}
CATEGORY_LABELS_JA = {
    "Women": "レディース", "Toys & Collectibles": "おもちゃ・コレクション", "Men": "メンズ", "Kids": "キッズ",
    "Electronics": "家電・ゲーム", "Home": "ホーム・インテリア", "Beauty": "コスメ・美容",
    "Vintage & collectibles": "ヴィンテージ・収集品", "Other": "その他", "Books": "本",
    "Sports & outdoors": "スポーツ・アウトドア", "Handmade": "ハンドメイド", "Arts & Crafts": "アート・クラフト",
    "Garden & Outdoor": "ガーデン・屋外", "Office": "オフィス用品", "Pet Supplies": "ペット用品", "Tools": "工具",
}

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- 読み込み
def _load_price(d: Path) -> dict[str, Any] | None:
    try:
        import joblib

        pipe = joblib.load(d / "price.joblib")
        metrics = json.loads((d / "price_metrics.json").read_text(encoding="utf-8"))
        aux = json.loads((d / "price_aux.json").read_text(encoding="utf-8"))
        q = aux["residual_quantiles_log"]
        out = {"pipe": pipe, "metrics": metrics, "aux": aux, "q25": float(q["q25"]), "q75": float(q["q75"]),
               "cat": {c["name"]: c for c in aux["categories"]}, "title_terms": None}
        try:  # 線形モデル(Ridge)のときだけ、タイトルの語ごとの寄与を出せる
            feats, reg = pipe.named_steps["features"], pipe.named_steps["reg"]
            vec = feats.named_transformers_["title"]
            out["title_terms"] = {"vec": vec, "coef": reg.coef_[feats.output_indices_["title"]],
                                  "names": vec.get_feature_names_out()}
        except Exception:
            pass
        return out
    except Exception as e:  # ファイルなし・バージョン不一致など
        logger.warning("price model unavailable: %s", e)
        return None


def _load_cluster(d: Path) -> dict[str, Any] | None:
    try:
        import joblib

        pipe = joblib.load(d / "cluster.joblib")
        info = json.loads((d / "cluster_info.json").read_text(encoding="utf-8"))
        labels = {int(c["cluster_id"]): str(c["label"]) for c in info["clusters"]}
        return {"pipe": pipe, "info": info, "labels": labels}
    except Exception as e:
        logger.warning("cluster model unavailable: %s", e)
        return None


def _state() -> dict[str, Any]:
    key = str(ARTIFACT_DIR)
    st = _cache.get(key)
    if st is None:
        with _lock:
            st = _cache.get(key)
            if st is None:
                d = Path(ARTIFACT_DIR)
                st = {"price": _load_price(d), "cluster": _load_cluster(d)}
                _cache[key] = st
    return st


def reset_cache() -> None:
    """読み込み済みのアーティファクトを捨てる(テスト・再学習後の再読み込み用)。"""
    with _lock:
        _cache.clear()


# --------------------------------------------------------------------------- 小道具
def _to_jpy(usd: float) -> int:
    """USD → 円(固定レート)。100 円単位に四捨五入し、最低 100 円。"""
    return max(ROUND_JPY, int(math.floor(usd * USD_JPY / ROUND_JPY + 0.5)) * ROUND_JPY)


def _yen(usd: float) -> str:
    return f"{_to_jpy(usd):,}円"


def _pct(ratio: float) -> str:
    return f"約{(ratio - 1) * 100:+.0f}%"


def _match(value: str | None, known: list[str]) -> str | None:
    v = (value or "").strip().lower()
    for k in known:
        if k.lower() == v:
            return k
    return None


def _norm_condition(condition: str | None) -> str | None:
    c = _match(condition, CONDITIONS)
    if c is None:
        ja = {v: k for k, v in CONDITION_LABELS_JA.items()}
        c = ja.get((condition or "").strip())
    return c


def _norm_category(category: str | None, known: list[str]) -> str | None:
    c = (category or "").strip()
    hit = _match(c, known)
    if hit is None and "/" in c:      # "Women/Shoes" のような形ならトップカテゴリだけ使う
        hit = _match(c.split("/")[0], known)
    if hit is None:
        ja = {v: k for k, v in CATEGORY_LABELS_JA.items()}
        hit = _match(ja.get(c), known)
    return hit


def _unavailable_price() -> dict:
    return {"available": False, "predicted_price": None, "low": None, "high": None, "currency": "JPY",
            "basis": [], "model": {"name": "unavailable", "mae": 0.0, "r2": 0.0, "trained_rows": 0}}


# --------------------------------------------------------------------------- 公開関数
def predict_price(title: str, category: str, brand: str | None, condition: str) -> dict:
    """出品価格の目安(円)を返す。

    - predicted_price: モデルの予測(log1p(USD) を戻して USD_JPY を掛け、100 円単位に丸めたもの)
    - low / high: validation 分割での残差(log 空間)の 25% 点・75% 点を予測に足したもの。
      test 分割で実際に入った割合は model_info()["price"]["interval"]["test_coverage"] を参照
    - basis: 学習データの実測統計と、モデルに条件を変えて入れ直した差(反実仮想)による説明
    - model.mae は test 分割の MAE を円に換算した値、model.r2 は log 目的変数での test R²
    """
    try:
        p = _state()["price"]
        if p is None:
            return _unavailable_price()
        import numpy as np

        aux, metrics, pipe = p["aux"], p["metrics"], p["pipe"]
        title = (title or "").strip()
        cat = _norm_category(category, list(p["cat"]))
        cond = _norm_condition(condition)
        brand_n = (brand or "").strip().lower()
        ref_cond = "Good" if cond == "New" else "New"

        row = [title, cat or "", brand_n, cond or ""]
        X = np.array([row, [title, cat or "", brand_n, ref_cond], [title, cat or "", "", cond or ""]], dtype=object)
        y, y_ref_cond, y_no_brand = (float(v) for v in pipe.predict(X))
        usd = max(math.expm1(y), 0.0)
        pred = _to_jpy(usd)
        low = min(_to_jpy(max(math.expm1(y + p["q25"]), 0.0)), pred)
        high = max(_to_jpy(max(math.expm1(y + p["q75"]), 0.0)), pred)

        overall = float(aux["overall_median_usd"])
        basis: list[dict] = []

        # 1) カテゴリ: 学習データでの中央値
        if cat:
            c = p["cat"][cat]
            basis.append({"feature": "カテゴリ", "value": cat,
                          "effect": f"学習データでこのカテゴリの価格の中央値は{_yen(c['median_usd'])}"
                                    f"({c['count']:,}件)。全体の中央値は{_yen(overall)}"})
        else:
            basis.append({"feature": "カテゴリ", "value": str(category or ""),
                          "effect": "モデルが知らないカテゴリのため、カテゴリの情報は推定に使われていません"})

        # 2) ブランド: 学習データでの中央値 + ブランドなしと比べたモデル上の差
        if brand_n:
            ratio = math.exp(y - y_no_brand)
            b = aux["brands"].get(brand_n)
            if b:
                eff = (f"学習データでこのブランドの価格の中央値は{_yen(b[0])}({b[1]:,}件)。"
                       f"モデル上はブランドなしの場合と比べて{_pct(ratio)}")
            else:
                eff = (f"学習データに{aux['brand_min_count']}件以上ないブランドのため、ブランド別の統計はありません。"
                       f"モデル上はブランドなしの場合と比べて{_pct(ratio)}")
            basis.append({"feature": "ブランド", "value": (brand or "").strip(), "effect": eff})

        # 3) 状態: 状態だけを変えてモデルに入れ直した差
        if cond:
            ratio = math.exp(y - y_ref_cond)
            basis.append({"feature": "状態", "value": cond,
                          "effect": f"同じ商品で状態だけを「{ref_cond}」に変えた場合と比べて、モデル上は{_pct(ratio)}"})
        else:
            basis.append({"feature": "状態", "value": str(condition or ""),
                          "effect": "モデルが知らない状態のため、状態の情報は推定に使われていません"})

        # 4) タイトル: 寄与が最も大きい語(線形モデルの 係数 × TF-IDF 値)
        tt = p["title_terms"]
        if tt is not None:
            r = tt["vec"].transform([title])
            if r.nnz == 0:
                basis.append({"feature": "タイトル", "value": title[:40],
                              "effect": "学習済みの単語(英語)が含まれないため、タイトルは推定に使われていません"})
            else:
                contrib = r.data * tt["coef"][r.indices]
                order = np.argsort(-np.abs(contrib))[:2]
                parts = [f"「{tt['names'][r.indices[i]]}」{_pct(math.exp(float(contrib[i])))}" for i in order]
                basis.append({"feature": "タイトルの語", "value": title[:40],
                              "effect": "価格への影響が大きい語(モデル上): " + "、".join(parts)})

        return {
            "available": True,
            "predicted_price": pred,
            "low": low,
            "high": high,
            "currency": "JPY",
            "basis": basis[:4],
            "model": {
                "name": str(metrics["model_name"]),
                "mae": round(float(metrics["test"]["mae_usd"]) * USD_JPY, 1),
                "r2": round(float(metrics["test"]["r2_log"]), 4),
                "trained_rows": int(metrics["trained_rows"]),
            },
        }
    except Exception as e:
        logger.warning("predict_price failed: %s", e)
        return _unavailable_price()


def assign_cluster(title: str, category: str) -> dict:
    """タイトル + カテゴリから商品クラスタを割り当てる。

    学習済みの単語が 1 つも含まれない場合(例: 日本語だけのタイトルで未知のカテゴリ)は
    根拠のある割り当てができないので available: False を返す。
    """
    none = {"available": False, "cluster_id": None, "cluster_label": None}
    try:
        c = _state()["cluster"]
        if c is None:
            return none
        price = _state()["price"]
        known = list(price["cat"]) if price else DEFAULT_CATEGORIES
        cat = _norm_category(category, known) or (category or "")
        text = f"{title or ''} {cat}".strip()
        if c["pipe"].named_steps["tfidf"].transform([text]).nnz == 0:
            return none
        cid = int(c["pipe"].predict([text])[0])
        return {"available": True, "cluster_id": cid, "cluster_label": c["labels"].get(cid)}
    except Exception as e:
        logger.warning("assign_cluster failed: %s", e)
        return none


def cluster_labels() -> dict[int, str]:
    c = _state()["cluster"]
    return dict(c["labels"]) if c else {}


def categories() -> list[str]:
    """モデルが知っているトップカテゴリ名(学習データでの件数が多い順)。"""
    p = _state()["price"]
    return list(p["cat"]) if p else list(DEFAULT_CATEGORIES)


def conditions() -> list[str]:
    """状態の名前(良い → 悪い)。"""
    return list(CONDITIONS)


def model_info() -> dict:
    """両モデルの指標(透明性ページ用)。"""
    st = _state()
    info: dict[str, Any] = {
        "usd_jpy": USD_JPY,
        "data_source": {
            "name": "MerRec (Mercari US)",
            "url": "https://huggingface.co/datasets/mercari-us/merrec",
            "license": "CC BY-NC 4.0",
        },
        "price": {"available": False},
        "cluster": {"available": False},
    }
    if st["price"]:
        m = st["price"]["metrics"]
        info["price"] = {
            "available": True,
            "model_name": m["model_name"],
            "target": m["target"],
            "features": m["features"],
            "split": m["split"],
            "test": m["test"],
            "test_mae_jpy": round(m["test"]["mae_usd"] * USD_JPY, 1),
            "baseline_test": m["baselines"]["median_per_category"]["test"],
            "baseline_test_mae_jpy": round(m["baselines"]["median_per_category"]["test"]["mae_usd"] * USD_JPY, 1),
            "test_unseen_title": m["test_unseen_title"],
            "interval": m["interval"],
            "data": m["data"],
        }
    if st["cluster"]:
        c = st["cluster"]["info"]
        info["cluster"] = {
            "available": True,
            "k": c["k"],
            "silhouette": c["silhouette"],
            "silhouette_note": c["silhouette_note"],
            "pipeline": c["pipeline"],
            "trained_rows": c["trained_rows"],
            "k_selection": c["k_selection"],
            "clusters": [{k: v for k, v in cl.items() if k != "examples"} for cl in c["clusters"]],
        }
    return info
