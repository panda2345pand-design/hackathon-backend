"""教師あり学習: 出品価格の予測(回帰)。

    .venv/bin/python -m ml.train_price

- 目的変数: log1p(価格 USD)
- 入力: タイトル / カテゴリ / ブランド / 状態(アプリの出品フォームにある項目だけ)
- 分割: 商品単位 train 80% / validation 10% / test 10%(seed 固定)
- 比較: 中央値ベースライン vs Ridge(疎な TF-IDF + one-hot) vs HistGradientBoosting(SVD + target encoding)
- モデル選択・ハイパーパラメータ・予測区間の幅は validation だけで決め、test は最後に 1 回だけ使う
- 出力: models_ml/price.joblib, models_ml/price_metrics.json, models_ml/price_aux.json
"""
from __future__ import annotations

import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, TargetEncoder

from ml.common import CONDITION_ORDER, MODEL_DIR, SEED, load_items, norm_brand, price_features, split_indices

TFIDF_MAX_FEATURES = 100_000   # 語彙の上限(アーティファクトを小さく保つため)
BRAND_MIN_FREQ = 20            # これ未満の出現数のブランドは「その他」にまとめる
RIDGE_ALPHAS = [0.3, 1.0, 3.0]
SUBCATEGORY_MIN_GAIN = 0.03    # top/sub を採用する条件: validation MAE(USD) が 3% 以上改善
BRAND_STAT_MIN_COUNT = 30      # 「根拠」表示用のブランド中央値を保存する最小件数


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def evaluate(y_log_true: np.ndarray, y_log_pred: np.ndarray) -> dict:
    p_true, p_pred = np.expm1(y_log_true), np.expm1(y_log_pred)
    return {
        "mae_usd": float(mean_absolute_error(p_true, p_pred)),
        "median_ae_usd": float(median_absolute_error(p_true, p_pred)),
        "r2_log": float(r2_score(y_log_true, y_log_pred)),
        "r2_usd": float(r2_score(p_true, p_pred)),
        "rmse_log": float(np.sqrt(np.mean((y_log_true - y_log_pred) ** 2))),
        "n": int(len(y_log_true)),
    }


def ridge_features() -> ColumnTransformer:
    return ColumnTransformer([
        ("title", TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=TFIDF_MAX_FEATURES,
                                  sublinear_tf=True, dtype=np.float32), 0),
        ("category", OneHotEncoder(handle_unknown="ignore"), [1]),
        ("brand", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=BRAND_MIN_FREQ), [2]),
        ("condition", OneHotEncoder(handle_unknown="ignore"), [3]),
    ])


def hgb_pipeline() -> Pipeline:
    title_svd = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=3, max_features=TFIDF_MAX_FEATURES,
                                  sublinear_tf=True, dtype=np.float32)),
        ("svd", TruncatedSVD(n_components=50, random_state=SEED)),
    ])
    feats = ColumnTransformer([
        ("title", title_svd, 0),
        ("cat_brand", TargetEncoder(target_type="continuous", random_state=SEED), [1, 2]),
        ("condition", OrdinalEncoder(categories=[CONDITION_ORDER], handle_unknown="use_encoded_value",
                                     unknown_value=-1), [3]),
    ])
    return Pipeline([("features", feats),
                     ("reg", HistGradientBoostingRegressor(max_iter=300, learning_rate=0.1, random_state=SEED))])


def group_median_baseline(train: pd.DataFrame, target: pd.DataFrame, keys: list[str]) -> np.ndarray:
    """keys ごとの train 価格中央値を予測値にする(未知の組は全体中央値)。log1p で返す。"""
    overall = float(train["price"].median())
    if not keys:
        return np.log1p(np.full(len(target), overall))
    med = train.groupby(keys)["price"].median().rename("m").reset_index()
    pred = target[keys].merge(med, on=keys, how="left")["m"].fillna(overall).to_numpy()
    return np.log1p(pred)


def main() -> None:
    t0 = time.time()
    df, data_stats = load_items()
    log("items", data_stats)
    y = np.log1p(df["price"].to_numpy())
    tr, va, te = split_indices(len(df))
    d_tr, d_va, d_te = df.iloc[tr], df.iloc[va], df.iloc[te]
    y_tr, y_va, y_te = y[tr], y[va], y[te]

    # ---- ベースライン(学習なし・中央値を返すだけ) ----
    baselines = {}
    for name, keys in [("global_median", []), ("median_per_category", ["c0"]),
                       ("median_per_category_sub", ["c0", "c1"])]:
        baselines[name] = {
            "validation": evaluate(y_va, group_median_baseline(d_tr, d_va, keys)),
            "test": evaluate(y_te, group_median_baseline(d_tr, d_te, keys)),
        }
        log("baseline", name, baselines[name]["test"])

    # ---- 候補モデル(validation で比較) ----
    candidates = []
    fitted = {}
    for mode in ["top", "top/sub"]:
        X_tr, X_va = price_features(d_tr, mode), price_features(d_va, mode)
        feats = ridge_features()
        A_tr = feats.fit_transform(X_tr, y_tr)
        A_va = feats.transform(X_va)
        for alpha in RIDGE_ALPHAS:
            t = time.time()
            reg = Ridge(alpha=alpha, solver="sparse_cg", tol=1e-4, random_state=SEED).fit(A_tr, y_tr)
            m = evaluate(y_va, reg.predict(A_va))
            name = f"ridge[{mode}] alpha={alpha}"
            candidates.append({"name": name, "family": "ridge", "category_mode": mode, "alpha": alpha,
                               "n_features": int(A_tr.shape[1]), "fit_seconds": round(time.time() - t, 1),
                               "validation": m})
            fitted[name] = (feats, reg)
            log(name, m)
    if "--skip-hgb" not in sys.argv:
        t = time.time()
        hgb = hgb_pipeline().fit(price_features(d_tr, "top"), y_tr)
        m = evaluate(y_va, hgb.predict(price_features(d_va, "top")))
        candidates.append({"name": "hist_gradient_boosting[top]", "family": "hgb", "category_mode": "top",
                           "fit_seconds": round(time.time() - t, 1), "validation": m})
        fitted["hist_gradient_boosting[top]"] = hgb
        log("hgb", m)

    # ---- 選択規則(事前に決めた規則。validation だけを見る) ----
    def best(mode=None):
        c = [x for x in candidates if mode is None or x["category_mode"] == mode]
        return min(c, key=lambda x: x["validation"]["rmse_log"])
    best_top, best_sub = best("top"), best("top/sub")
    gain = 1 - best_sub["validation"]["mae_usd"] / best_top["validation"]["mae_usd"]
    chosen = best_sub if gain >= SUBCATEGORY_MIN_GAIN else best_top
    log("chosen", chosen["name"], "subcategory MAE gain", round(gain, 4))

    obj = fitted[chosen["name"]]
    pipe = obj if isinstance(obj, Pipeline) else Pipeline([("features", obj[0]), ("reg", obj[1])])
    mode = chosen["category_mode"]
    X_va, X_te = price_features(d_va, mode), price_features(d_te, mode)

    # ---- 予測区間: validation の残差(log 空間)の分位点 ----
    res_va = y_va - pipe.predict(X_va)
    q = {f"q{int(p * 100):02d}": float(np.quantile(res_va, p)) for p in [0.10, 0.25, 0.50, 0.75, 0.90]}

    # ---- test(最後に 1 回) ----
    pred_te = pipe.predict(X_te)
    test_metrics = evaluate(y_te, pred_te)
    res_te = y_te - pred_te
    coverage = {
        "q25_q75": float(np.mean((res_te >= q["q25"]) & (res_te <= q["q75"]))),
        "q10_q90": float(np.mean((res_te >= q["q10"]) & (res_te <= q["q90"]))),
    }
    # train に同じタイトル(小文字化)が 1 件もない test 商品だけでの成績(重複出品による甘さの確認)
    seen = set(d_tr["title"].str.lower().str.strip())
    unseen = ~d_te["title"].str.lower().str.strip().isin(seen).to_numpy()
    test_unseen = evaluate(y_te[unseen], pred_te[unseen])
    base_unseen = evaluate(y_te[unseen], group_median_baseline(d_tr, d_te, ["c0"])[unseen])
    log("test", test_metrics, "unseen-title", test_unseen, "coverage", coverage)

    # ---- 保存 ----
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, "price.joblib")
    joblib.dump(pipe, path, compress=3)
    size_mb = os.path.getsize(path) / 1e6

    # 「根拠」表示用の集計(すべて train 分割の実測値。USD)
    overall_median = float(d_tr["price"].median())
    cat_stats = d_tr.groupby("c0")["price"].agg(["median", "count"]).sort_values("count", ascending=False)
    cond_stats = d_tr.groupby("condition")["price"].agg(["median", "count"])
    b = d_tr.assign(b=d_tr["brand"].map(norm_brand))
    b = b[b["b"] != ""].groupby("b")["price"].agg(["median", "count"])
    b = b[b["count"] >= BRAND_STAT_MIN_COUNT]
    aux = {
        "overall_median_usd": overall_median,
        "category_mode": mode,
        "categories": [{"name": k, "median_usd": float(r["median"]), "count": int(r["count"])}
                       for k, r in cat_stats.iterrows()],
        "conditions": [{"name": c, "median_usd": float(cond_stats.loc[c, "median"]),
                        "count": int(cond_stats.loc[c, "count"])} for c in CONDITION_ORDER],
        "brand_min_count": BRAND_STAT_MIN_COUNT,
        "brands": {k: [float(r["median"]), int(r["count"])] for k, r in b.iterrows()},
        "residual_quantiles_log": q,
    }
    if mode == "top/sub":
        sub = d_tr.groupby(["c0", "c1"]).size().sort_values(ascending=False)
        aux["subcategories"] = [f"{a}/{c}" for (a, c), _ in sub.items()]
    with open(os.path.join(MODEL_DIR, "price_aux.json"), "w") as f:
        json.dump(aux, f, ensure_ascii=False)

    metrics = {
        "model_name": chosen["name"],
        "model_family": chosen["family"],
        "target": "log1p(price_usd)",
        "features": ["title (TF-IDF word 1-2gram)", f"category ({mode})", "brand", "condition"],
        "category_mode": mode,
        "subcategory_validation_mae_gain": float(gain),
        "subcategory_min_gain_rule": SUBCATEGORY_MIN_GAIN,
        "data": data_stats,
        "split": {"seed": SEED, "train": int(len(tr)), "validation": int(len(va)), "test": int(len(te))},
        "trained_rows": int(len(tr)),
        "price_usd_summary_train": {k: float(v) for k, v in
                                    d_tr["price"].describe(percentiles=[.05, .25, .5, .75, .95]).items()},
        "baselines": baselines,
        "candidates": candidates,
        "test": test_metrics,
        "test_unseen_title": {"model": test_unseen, "median_per_category": base_unseen},
        "interval": {"method": "validation residual quantiles in log1p space, added to the prediction",
                     "residual_quantiles_log": q, "test_coverage": coverage},
        "artifact": {"file": "price.joblib", "size_mb": round(size_mb, 2)},
        "versions": {"python": sys.version.split()[0], "scikit_learn": sklearn.__version__,
                     "numpy": np.__version__, "pandas": pd.__version__},
        "train_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(MODEL_DIR, "price_metrics.json"), "w") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=1)
    log("saved", path, f"{size_mb:.2f} MB", "total sec", metrics["train_seconds"])


if __name__ == "__main__":
    main()
