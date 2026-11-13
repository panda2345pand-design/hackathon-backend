"""教師なし学習: 商品のクラスタリング(似た商品のグループ分け)。

    .venv/bin/python -m ml.train_cluster

- 入力: 「タイトル + トップカテゴリ名」の文字列(アプリで推論時に手に入る情報だけ)
- TF-IDF → TruncatedSVD(50 次元, LSA) → L2 正規化 → KMeans
- TF-IDF と SVD は全商品で学習。KMeans は固定の 30 万件サンプルで学習し、全商品に割り当てる
- k は 8..40(2 刻み)で、慣性(inertia)とシルエット係数(固定サンプル)を測って決める
  規則: シルエット係数が「最大値 - 0.01」以上になる最小の k を採用
  (最大値付近は差が小さく横ばいなので、同程度なら少ないクラスタ数を選ぶ。
   証拠の表は cluster_info.json の k_selection に保存)
- 出力: models_ml/cluster.joblib, models_ml/cluster_info.json
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import joblib
import numpy as np
import sklearn
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import Normalizer

from ml.common import MODEL_DIR, SEED, cluster_text, load_items

TFIDF_MAX_FEATURES = 20_000
SVD_COMPONENTS = 50
K_RANGE = list(range(8, 41, 2))   # 採用候補
K_PROBE = [50, 60, 80, 100]       # 参考: 40 より先でシルエットがまだ伸びるかを見るだけ(採用しない。
                                  # 画面で人が見分けるラベルとして 40 個を上限にしたため)
KSEARCH_SAMPLE = 300_000      # KMeans を当てはめる件数(k 探索・最終モデル共通)
SILHOUETTE_TOLERANCE = 0.01   # 最大シルエットからこの幅以内なら「同程度」とみなす
SILHOUETTE_SAMPLE = 20_000    # シルエット係数を計算する件数(計算量が件数の 2 乗のため)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def kmeans(k: int, n_init: int) -> KMeans:
    return KMeans(n_clusters=k, random_state=SEED, n_init=n_init, max_iter=300)


def main() -> None:
    t0 = time.time()
    df, data_stats = load_items()
    texts = [cluster_text(t, c) for t, c in zip(df["title"], df["c0"])]
    n = len(texts)

    tfidf = TfidfVectorizer(stop_words="english", min_df=5, max_features=TFIDF_MAX_FEATURES,
                            sublinear_tf=True, dtype=np.float32)
    A = tfidf.fit_transform(texts)
    svd = TruncatedSVD(n_components=SVD_COMPONENTS, random_state=SEED)
    Z = Normalizer(copy=False).fit_transform(svd.fit_transform(A)).astype(np.float32)
    explained = float(svd.explained_variance_ratio_.sum())
    log("tfidf", A.shape, "svd explained variance", round(explained, 4))

    rng = np.random.RandomState(SEED)
    idx_fit = rng.choice(n, size=min(KSEARCH_SAMPLE, n), replace=False)
    idx_sil = idx_fit[:SILHOUETTE_SAMPLE]
    Z_fit, Z_sil = Z[idx_fit], Z[idx_sil]

    # ---- k の探索 ----
    table = []
    for k in K_RANGE + K_PROBE:
        km = kmeans(k, n_init=3).fit(Z_fit)
        inertia = float(km.inertia_)           # クラスタ内平方和
        sil = float(silhouette_score(Z_sil, km.predict(Z_sil), metric="euclidean"))
        table.append({"k": k, "inertia": inertia, "silhouette": sil, "candidate": k in K_RANGE})
        log(table[-1])
    for i, row in enumerate(table):  # 肘の目安: 直前の k からの慣性の減少率
        row["inertia_drop_pct"] = None if i == 0 else round(100 * (1 - row["inertia"] / table[i - 1]["inertia"]), 2)
    cand = [r for r in table if r["candidate"]]
    sil_max = max(r["silhouette"] for r in cand)
    k = min(r["k"] for r in cand if r["silhouette"] >= sil_max - SILHOUETTE_TOLERANCE)
    log("chosen k", k, "max silhouette", sil_max)

    # ---- 最終モデル(同じサンプルで n_init を増やして学習し、全商品に割り当てる) ----
    km = kmeans(k, n_init=5).fit(Z_fit)
    labels = km.predict(Z)
    sil_final = float(silhouette_score(Z_sil, labels[idx_sil], metric="euclidean"))

    svd.components_ = svd.components_.astype(np.float32)  # 保存サイズを半分に
    pipe = Pipeline([("tfidf", tfidf), ("svd", svd), ("norm", Normalizer()), ("kmeans", km)])
    check = pipe.predict(texts[:5000])
    agree = float(np.mean(check == labels[:5000]))

    # ---- クラスタごとの説明 ----
    vocab = np.array(tfidf.get_feature_names_out())
    c0 = df["c0"].to_numpy()
    cat_tokens = set(re.findall(r"\w\w+", " ".join(set(c0)).lower()))
    clusters = []
    for c in range(k):
        m = labels == c
        mean_tfidf = np.asarray(A[m].mean(axis=0)).ravel()
        order = np.argsort(-mean_tfidf)
        # ラベルにはカテゴリ名そのものの語(women など)を使わない
        terms = [str(vocab[j]) for j in order if vocab[j] not in cat_tokens][:10]
        cats, counts = np.unique(c0[m], return_counts=True)
        top = int(np.argmax(counts))
        share = float(counts[top] / m.sum())
        clusters.append({
            "cluster_id": c,
            "label": f"{cats[top]}: {' / '.join(terms[:3])}",
            "top_terms": terms,
            "size": int(m.sum()),
            "share": float(m.mean()),
            "dominant_category": str(cats[top]),
            "dominant_category_share": share,
            "median_price_usd": float(np.median(df["price"].to_numpy()[m])),
            "examples": [str(t) for t in df["title"].to_numpy()[m][:3]],
        })

    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, "cluster.joblib")
    joblib.dump(pipe, path, compress=3)
    size_mb = os.path.getsize(path) / 1e6
    info = {
        "k": k,
        "silhouette": sil_final,
        "silhouette_note": f"euclidean on L2-normalized SVD vectors, fixed random sample of {len(idx_sil)} items",
        "selection_rule": f"smallest candidate k (8..40) with silhouette >= max - {SILHOUETTE_TOLERANCE}",
        "silhouette_max_in_search": sil_max,
        "k_selection": table,
        "pipeline": ["TfidfVectorizer(title + top category, english stop words)",
                     f"TruncatedSVD({SVD_COMPONENTS})", "Normalizer(l2)", f"KMeans(k={k})"],
        "tfidf_vocabulary": int(A.shape[1]),
        "svd_explained_variance": explained,
        "trained_rows": n,
        "ksearch_rows": int(len(idx_fit)),
        "pipeline_reload_agreement": agree,
        "clusters": clusters,
        "data": data_stats,
        "artifact": {"file": "cluster.joblib", "size_mb": round(size_mb, 2)},
        "versions": {"python": sys.version.split()[0], "scikit_learn": sklearn.__version__, "numpy": np.__version__},
        "train_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(MODEL_DIR, "cluster_info.json"), "w") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    log("saved", path, f"{size_mb:.2f} MB", "silhouette", round(sil_final, 4), "agree", agree)
    for c in clusters:
        log(c["cluster_id"], c["size"], c["label"], round(c["dominant_category_share"], 2))


if __name__ == "__main__":
    main()
