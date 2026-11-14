"""教師あり学習(2 本目): 「いいねされやすさ」= 出品が 1 回以上いいねされる確率(2 値分類)。

    .venv/bin/python -m ml.train_like            # 全部(感度分析つき)
    .venv/bin/python -m ml.train_like --quick    # 感度分析を省く(出荷判定は同じ)

単位とラベル
- 1 行 = 1 商品(item_id)。ラベル = その商品に item_like のイベントが 1 回以上あるか。
- 主定義(モデルを見る前に決めた): 「ログの中で 1 回以上 閲覧(item_view)された商品」だけを対象にする。
  閲覧 0 回の商品は「いいねされたからログに載った」商品がほとんどで、ラベルで選ばれてしまっているため除く。
  代わりの定義(閲覧 0 回も含む全商品)の数字も同じ手順で出して並べる。
- 閲覧回数は出品時には分からないので特徴量に入れない。どれだけラベルを説明するかだけ報告する。

特徴量(出品フォームにある項目だけ)
- タイトル(TF-IDF 単語 1-2gram)/ トップカテゴリ / ブランド / 状態 / 価格(log)/ 相対価格
  (log 価格 − 同じ「カテゴリ×ブランド」の train の log 価格中央値。少ない組はカテゴリ、さらに無ければ全体)

分割
- タイトル(小文字化・前後空白除去)が同じ商品は同じ側に入れる(グループ分割)。train 80 / validation 10 / test 10。
- 語彙・ブランドの一覧・中央値・標準化・基準率はすべて train だけで作る。C は validation で選び、test は最後に 1 回。

出荷の規則(事前に固定): test ROC-AUC >= 0.65 かつ カテゴリだけのベースラインより +0.03 以上 かつ
  較正が妥当(予測 10 分位の実測いいね率が単調増加、Brier が基準率予測より良い)。
満たしたときだけ models_ml/like.joblib と models_ml/like_metrics.json を書く。
満たさないときは何も保存せず、結果の JSON を標準出力(と --out で指定したファイル)に出すだけ。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
import scipy.sparse as sp
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from ml.common import CONDITION_ORDER, DATA_DIR, MODEL_DIR, SEED, norm_brand

# ---- 事前に固定した設定 ----
MIN_VIEWS = 1                   # 主定義: 閲覧 1 回以上の商品
SHIP_MIN_AUC = 0.65
SHIP_MIN_GAIN_OVER_CATEGORY = 0.03
MAX_ARTIFACT_MB = 6.0
TFIDF_MAX_FEATURES = 50_000
TFIDF_MIN_DF = 5
BRAND_MIN_FREQ = 20             # これ未満のブランドは「その他」
SUBCAT_MIN_FREQ = 20
MEDIAN_MIN_COUNT = 30           # カテゴリ×ブランドの中央値を使う最小件数
C_GRID = [0.03, 0.1, 0.3, 1.0]
REL_CLIP = 3.0
LEVEL_HIGH = 1.25               # 確率 >= 基準率 × 1.25 → high
LEVEL_LOW = 0.80                # 確率 <  基準率 × 0.80 → low
BRAND_NONE, BRAND_OTHER = "", "\x00other"

RAW = ["user_id", "item_id", "product_id", "stime", "event_id", "name", "price",
       "c0_name", "c1_name", "c2_name", "brand_name", "item_condition_name"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True, file=sys.stderr)


# --------------------------------------------------------------------------- データ
def load_items(data_dir: str = DATA_DIR) -> tuple[pd.DataFrame, dict]:
    """全シャードを読み、item_id ごとに 1 行(属性は最新イベントの値、ラベルと閲覧回数は全イベントから)。"""
    files = sorted(glob.glob(os.path.join(data_dir, "merrec*.parquet")))
    if not files:
        raise FileNotFoundError(f"no merrec*.parquet in {data_dir}")
    raw = pd.concat([pd.read_parquet(f, columns=RAW) for f in files], ignore_index=True)
    stats = {
        "files": [os.path.basename(f) for f in files],
        "event_rows": int(len(raw)),
        "users": int(raw["user_id"].nunique()),
        "date_min": str(raw["stime"].min()), "date_max": str(raw["stime"].max()),
        "event_counts": {k: int(v) for k, v in raw["event_id"].value_counts().items()},
    }
    raw["is_like"] = raw["event_id"].eq("item_like")
    raw["is_view"] = raw["event_id"].eq("item_view")
    raw = raw.sort_values("stime", kind="stable")
    g = raw.groupby("item_id", sort=True)
    agg = g.agg(liked=("is_like", "max"), views=("is_view", "sum"), events=("is_like", "size"),
                n_users=("user_id", "nunique"), first_user=("user_id", "first"))
    last = raw.drop_duplicates("item_id", keep="last").set_index("item_id").sort_index()
    items = agg.join(last[["product_id", "name", "price", "c0_name", "c1_name", "c2_name",
                           "brand_name", "item_condition_name"]])
    stats["unique_items"] = int(len(items))
    stats["events_per_item_mean"] = float(items["events"].mean())
    stats["items_with_single_event_share"] = float((items["events"] == 1).mean())
    stats["items_seen_by_single_user_share"] = float((items["n_users"] == 1).mean())
    u = raw.groupby("user_id")["is_like"].sum().sort_values(ascending=False)
    stats["top1pct_users_share_of_likes"] = float(u.head(max(1, len(u) // 100)).sum() / u.sum())
    stats["users_with_any_like_share"] = float((u > 0).mean())

    ok = (items["item_condition_name"].isin(CONDITION_ORDER) & items["price"].gt(0)
          & items["name"].notna() & items["c0_name"].notna())
    stats["dropped_invalid"] = int((~ok).sum())
    items = items[ok]
    df = pd.DataFrame({
        "item_id": items.index.to_numpy(),
        "title": items["name"].astype(str).to_numpy(),
        "c0": items["c0_name"].astype(str).to_numpy(),
        "c1": items["c1_name"].fillna("").astype(str).to_numpy(),
        "c2": items["c2_name"].fillna("").astype(str).to_numpy(),
        "brand": items["brand_name"].fillna("").astype(str).map(norm_brand).to_numpy(),
        "condition": items["item_condition_name"].astype(str).to_numpy(),
        "price": items["price"].astype(float).to_numpy(),
        "product_id": items["product_id"].astype(str).to_numpy(),
        "first_user": items["first_user"].to_numpy(),
        "views": items["views"].astype(int).to_numpy(),
        "y": items["liked"].astype(int).to_numpy(),
    })
    df["title_key"] = df["title"].str.lower().str.strip()
    stats["items_valid"] = int(len(df))
    return df, stats


def view_bucket_table(df: pd.DataFrame) -> list[dict]:
    b = pd.cut(df["views"], [-1, 0, 1, 2, 3, 5, 10, 10**9], labels=["0", "1", "2", "3", "4-5", "6-10", "11+"])
    t = df.groupby(b, observed=True)["y"].agg(["size", "mean"])
    return [{"views": str(k), "items": int(r["size"]), "like_rate": float(r["mean"])} for k, r in t.iterrows()]


def group_split(keys: np.ndarray, seed: int = SEED, val: float = 0.1, test: float = 0.1):
    """同じキーの商品が同じ側に入る分割。グループ単位で 80/10/10。"""
    uniq, inv = np.unique(keys, return_inverse=True)
    perm = np.random.RandomState(seed).permutation(len(uniq))
    n_te, n_va = int(round(len(uniq) * test)), int(round(len(uniq) * val))
    side = np.zeros(len(uniq), dtype=np.int8)          # 0 train / 1 val / 2 test
    side[perm[:n_te]] = 2
    side[perm[n_te:n_te + n_va]] = 1
    s = side[inv]
    return np.flatnonzero(s == 0), np.flatnonzero(s == 1), np.flatnonzero(s == 2)


# --------------------------------------------------------------------------- 特徴量
def _vocab(values: pd.Series, min_freq: int) -> dict[str, int]:
    vc = values.value_counts()
    return {k: i for i, k in enumerate(sorted(vc[vc >= min_freq].index))}


def _onehot(values: np.ndarray, vocab: dict[str, int], other: int | None = None) -> sp.csr_matrix:
    n_cols = len(vocab) + (1 if other is not None else 0)
    idx = np.array([vocab.get(v, -1 if other is None else other) for v in values], dtype=np.int64)
    rows = np.flatnonzero(idx >= 0)
    return sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, idx[rows])), shape=(len(values), n_cols))


def fit_pre(d: pd.DataFrame) -> dict:
    """train だけから前処理を作る。"""
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=TFIDF_MIN_DF, max_features=TFIDF_MAX_FEATURES,
                          sublinear_tf=True, dtype=np.float32).fit(d["title"])
    lp = np.log(d["price"].to_numpy())
    tmp = pd.DataFrame({"c0": d["c0"].to_numpy(), "brand": d["brand"].to_numpy(), "lp": lp})
    cb = tmp[tmp["brand"] != ""].groupby(["c0", "brand"])["lp"].agg(["median", "size"])
    cb = cb[cb["size"] >= MEDIAN_MIN_COUNT]
    brand_vocab = _vocab(d["brand"][d["brand"] != ""], BRAND_MIN_FREQ)
    brand_vocab[BRAND_NONE] = len(brand_vocab)
    pre = {
        "vec": vec,
        "c0": _vocab(d["c0"], 1),
        "c1": _vocab(d["c0"] + "/" + d["c1"], SUBCAT_MIN_FREQ),
        "c2": _vocab(d["c0"] + "/" + d["c1"] + "/" + d["c2"], SUBCAT_MIN_FREQ),
        "brand": brand_vocab,
        "cond": {c: i for i, c in enumerate(CONDITION_ORDER)},
        "med_overall": float(np.median(lp)),
        "med_c0": {k: float(v) for k, v in tmp.groupby("c0")["lp"].median().items()},
        "med_c0_brand": {f"{a}\t{b}": float(m) for (a, b), m in cb["median"].items()},
        "lp_center": float(lp.mean()),
    }
    num = _numeric_raw(d, pre)
    pre["num_mean"], pre["num_std"] = num.mean(0), num.std(0)
    return pre


def _ref_logprice(c0: np.ndarray, brand: np.ndarray, pre: dict) -> np.ndarray:
    cb, c, o = pre["med_c0_brand"], pre["med_c0"], pre["med_overall"]
    return np.array([cb.get(f"{a}\t{b}", c.get(a, o)) if b else c.get(a, o) for a, b in zip(c0, brand)])


def _numeric_from(lp: np.ndarray, ref: np.ndarray, pre: dict) -> np.ndarray:
    """[log 価格, (log 価格 − 平均)^2, 相対価格, 相対価格^2]。相対価格 = log 価格 − 似た商品の log 中央値。"""
    rel = np.clip(lp - ref, -REL_CLIP, REL_CLIP)
    return np.column_stack([lp, (lp - pre["lp_center"]) ** 2, rel, rel ** 2])


def _numeric_raw(d: pd.DataFrame, pre: dict, price: np.ndarray | None = None) -> np.ndarray:
    p = d["price"].to_numpy() if price is None else price
    return _numeric_from(np.log(p), _ref_logprice(d["c0"].to_numpy(), d["brand"].to_numpy(), pre), pre)


def blocks(d: pd.DataFrame, pre: dict, price: np.ndarray | None = None) -> dict[str, sp.csr_matrix]:
    num = (_numeric_raw(d, pre, price) - pre["num_mean"]) / pre["num_std"]
    return {
        "title": pre["vec"].transform(d["title"]),
        "c0": _onehot(d["c0"].to_numpy(), pre["c0"]),
        "c1": _onehot((d["c0"] + "/" + d["c1"]).to_numpy(), pre["c1"]),
        "c2": _onehot((d["c0"] + "/" + d["c1"] + "/" + d["c2"]).to_numpy(), pre["c2"]),
        "brand": _onehot(d["brand"].to_numpy(), pre["brand"], other=len(pre["brand"])),
        "cond": _onehot(d["condition"].to_numpy(), pre["cond"]),
        "num": sp.csr_matrix(num.astype(np.float32)),
    }


MAIN = ["title", "c0", "brand", "cond", "num"]     # アプリの出品フォームで入力できる項目だけ


def stack(b: dict, names: list[str]) -> sp.csr_matrix:
    return sp.hstack([b[n] for n in names], format="csr")


def fit_lr(X, y, C: float) -> LogisticRegression:
    return LogisticRegression(C=C, solver="lbfgs", max_iter=500, tol=1e-4).fit(X, y)


# --------------------------------------------------------------------------- 評価
def scores(y: np.ndarray, p: np.ndarray) -> dict:
    return {"roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
            "brier": float(brier_score_loss(y, p)), "n": int(len(y)), "positive_rate": float(y.mean())}


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    order = np.argsort(p, kind="stable")
    out = []
    for i, idx in enumerate(np.array_split(order, bins)):
        out.append({"decile": i + 1, "n": int(len(idx)), "predicted_mean": float(p[idx].mean()),
                    "observed_rate": float(y[idx].mean())})
    return out


def rate_baseline(train: pd.DataFrame, target: pd.DataFrame, keys: list[str], prior: float, k: float = 20.0):
    """keys ごとの train いいね率(件数が少ない組は全体の率に寄せる: (いいね数 + k×全体率)/(件数 + k))。"""
    g = train.groupby(keys)["y"].agg(["sum", "size"]).reset_index()
    g["r"] = (g["sum"] + k * prior) / (g["size"] + k)
    return target[keys].merge(g[keys + ["r"]], on=keys, how="left")["r"].fillna(prior).to_numpy()


def price_direction_raw(d: pd.DataFrame, pre: dict) -> dict:
    """生データでの価格と「いいね」の関係(モデルを通さない)。"""
    rel = np.log(d["price"].to_numpy()) - _ref_logprice(d["c0"].to_numpy(), d["brand"].to_numpy(), pre)
    t = pd.DataFrame({"c0": d["c0"].to_numpy(), "pid": d["product_id"].to_numpy(), "y": d["y"].to_numpy(),
                      "rel": rel, "views": d["views"].to_numpy(), "lp": np.log(d["price"].to_numpy())})
    edges = [-np.inf, -1.0, -0.5, -0.2, 0.2, 0.5, 1.0, np.inf]
    labels = ["<-1.0", "-1.0..-0.5", "-0.5..-0.2", "-0.2..0.2", "0.2..0.5", "0.5..1.0", ">1.0"]
    t["bin"] = pd.cut(t["rel"], edges, labels=labels)
    by_bin = [{"rel_log_price": str(k), "items": int(r["size"]), "like_rate": float(r["mean"]),
               "mean_views": float(v)} for (k, r), v in
              zip(t.groupby("bin", observed=True)["y"].agg(["size", "mean"]).iterrows(),
                  t.groupby("bin", observed=True)["views"].mean())]
    one = t[t["views"] == 1]
    by_bin_1v = [{"rel_log_price": str(k), "items": int(r["size"]), "like_rate": float(r["mean"])}
                 for k, r in one.groupby("bin", observed=True)["y"].agg(["size", "mean"]).iterrows()]

    def halves(frame: pd.DataFrame, key: str, min_n: int) -> dict:
        f = frame[frame.groupby(key)[key].transform("size") >= min_n]
        med = f.groupby(key)["lp"].transform("median")
        lo, hi = f[f["lp"] < med], f[f["lp"] > med]
        a = lo.groupby(key)["y"].agg(["mean", "size"])
        b = hi.groupby(key)["y"].agg(["mean", "size"])
        j = a.join(b, lsuffix="_lo", rsuffix="_hi", how="inner")
        w = j["size_lo"] + j["size_hi"]
        diff = j["mean_lo"] - j["mean_hi"]
        return {"groups": int(len(j)), "items": int(w.sum()),
                "like_rate_cheaper_half": float((j["mean_lo"] * j["size_lo"]).sum() / j["size_lo"].sum()),
                "like_rate_pricier_half": float((j["mean_hi"] * j["size_hi"]).sum() / j["size_hi"].sum()),
                "weighted_mean_diff_cheaper_minus_pricier": float((diff * w).sum() / w.sum()),
                "groups_where_cheaper_half_liked_more_share": float((diff > 0).mean())}

    return {
        "by_relative_price_bin": by_bin,
        "by_relative_price_bin_single_view_items": by_bin_1v,
        "within_category_halves": halves(t, "c0", 100),
        "within_product_id_halves": halves(t, "pid", 40),
        "within_product_id_halves_single_view_items": halves(one, "pid", 40),
    }


def price_effect_model(d: pd.DataFrame, pre: dict, clf: LogisticRegression, B: dict, names: list[str]) -> dict:
    """他の特徴量を固定して価格だけ 0.8 倍 / 1.2 倍にしたときの予測確率の変化。"""
    base = clf.predict_proba(stack(B, names))[:, 1]
    out = {}
    for label, f in [("x0.8", 0.8), ("x1.2", 1.2)]:
        num = (_numeric_raw(d, pre, d["price"].to_numpy() * f) - pre["num_mean"]) / pre["num_std"]
        p = clf.predict_proba(stack({**B, "num": sp.csr_matrix(num.astype(np.float32))}, names))[:, 1]
        dlt = p - base
        out[label] = {"mean_change": float(dlt.mean()), "median_change": float(np.median(dlt)),
                      "p05_change": float(np.quantile(dlt, 0.05)), "p95_change": float(np.quantile(dlt, 0.95)),
                      "share_probability_up": float((dlt > 0).mean()),
                      "mean_relative_change": float((p / base - 1).mean())}
    out["mean_probability_at_current_price"] = float(base.mean())
    return out


def quick_experiment(df: pd.DataFrame, keys: np.ndarray, C: float, label: str) -> dict:
    """別の分割(感度分析)で、同じ特徴量・同じ C のモデルと カテゴリだけのベースラインの test AUC を出す。"""
    tr, _, te = group_split(keys)
    d_tr, d_te = df.iloc[tr], df.iloc[te]
    pre = fit_pre(d_tr)
    clf = fit_lr(stack(blocks(d_tr, pre), MAIN), d_tr["y"].to_numpy(), C)
    p = clf.predict_proba(stack(blocks(d_te, pre), MAIN))[:, 1]
    y = d_te["y"].to_numpy()
    prior = float(d_tr["y"].mean())
    cat = rate_baseline(d_tr, d_te, ["c0"], prior)
    r = {"train": int(len(tr)), "test": int(len(te)), "model": scores(y, p),
         "category_only_roc_auc": float(roc_auc_score(y, cat)), "n_iter": int(clf.n_iter_[0])}
    log(label, r)
    return r


# --------------------------------------------------------------------------- 本体
def main() -> None:
    t0 = time.time()
    quick = "--quick" in sys.argv
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    all_items, data = load_items()
    log("loaded", data)
    data["like_rate_all_valid_items"] = float(all_items["y"].mean())
    exposure = {"like_rate_by_view_count_all_items": view_bucket_table(all_items),
                "views_only_roc_auc_all_items": float(roc_auc_score(all_items["y"], all_items["views"]))}

    df = all_items[all_items["views"] >= MIN_VIEWS].reset_index(drop=True)
    data["items_used_main"] = int(len(df))
    data["items_excluded_zero_views"] = int(len(all_items) - len(df))
    data["like_rate_excluded_zero_view_items"] = float(all_items.loc[all_items["views"] < MIN_VIEWS, "y"].mean())
    data["like_rate_main"] = float(df["y"].mean())
    data["title_duplicate_share_main"] = float(df["title_key"].duplicated(keep=False).mean())
    data["brand_missing_rate_main"] = float((df["brand"] == "").mean())
    exposure["views_only_roc_auc_main_items"] = float(roc_auc_score(df["y"], df["views"]))

    tr, va, te = group_split(df["title_key"].to_numpy())
    d_tr, d_va, d_te = df.iloc[tr], df.iloc[va], df.iloc[te]
    y_tr, y_va, y_te = (d["y"].to_numpy() for d in (d_tr, d_va, d_te))
    assert not (set(d_tr["title_key"]) & set(d_te["title_key"])) and not (set(d_tr["item_id"]) & set(d_te["item_id"]))
    base_rate = float(y_tr.mean())
    log("split", len(tr), len(va), len(te), "base rate", base_rate)

    pre = fit_pre(d_tr)
    B_tr, B_va, B_te = blocks(d_tr, pre), blocks(d_va, pre), blocks(d_te, pre)

    # ---- C を validation で選ぶ(ROC-AUC 最大。同点なら小さい C)----
    candidates, fitted = [], {}
    for C in C_GRID:
        t = time.time()
        clf = fit_lr(stack(B_tr, MAIN), y_tr, C)
        m = scores(y_va, clf.predict_proba(stack(B_va, MAIN))[:, 1])
        candidates.append({"C": C, "validation": m, "fit_seconds": round(time.time() - t, 1),
                           "n_iter": int(clf.n_iter_[0])})
        fitted[C] = clf
        log("C", C, m, "iter", clf.n_iter_[0])
    best = max(candidates, key=lambda c: (round(c["validation"]["roc_auc"], 4), -c["C"]))
    C, clf = best["C"], fitted[best["C"]]

    # ---- 特徴量を抜いた比較と、サブカテゴリを足した参考モデル(同じ C。validation と test)----
    variants = {}
    for name, cols in [("without_price", ["title", "c0", "brand", "cond"]),
                       ("without_title", ["c0", "brand", "cond", "num"]),
                       ("category_brand_condition_only", ["c0", "brand", "cond"]),
                       ("price_only", ["num"]),
                       ("reference_with_subcategories_c1_c2", MAIN + ["c1", "c2"])]:
        m_ = fit_lr(stack(B_tr, cols), y_tr, C)
        variants[name] = {"validation": scores(y_va, m_.predict_proba(stack(B_va, cols))[:, 1]),
                          "test": scores(y_te, m_.predict_proba(stack(B_te, cols))[:, 1])}
        log("variant", name, variants[name]["test"])

    # ---- test(最後に 1 回)----
    p_te = clf.predict_proba(stack(B_te, MAIN))[:, 1]
    test = scores(y_te, p_te)
    rel = reliability(y_te, p_te)
    obs = [r["observed_rate"] for r in rel]
    monotone = all(b >= a for a, b in zip(obs, obs[1:]))
    baselines = {"base_rate": scores(y_te, np.full(len(y_te), base_rate))}
    for name, keys in [("category_only", ["c0"]), ("category_sub_only_c0_c1_c2", ["c0", "c1", "c2"]),
                       ("category_brand_rate", ["c0", "brand"])]:
        baselines[name] = scores(y_te, rate_baseline(d_tr, d_te, keys, base_rate))
    gain = test["roc_auc"] - baselines["category_only"]["roc_auc"]
    log("test", test, "category-only", baselines["category_only"]["roc_auc"], "monotone", monotone)

    # 露出(閲覧回数)で層に分けた test の成績。閲覧 1 回の層 = 露出がそろった比較
    v_te = d_te["views"].to_numpy()
    strata = {}
    for name, mask in [("views_eq_1", v_te == 1), ("views_eq_2", v_te == 2), ("views_ge_3", v_te >= 3)]:
        strata[name] = {**scores(y_te[mask], p_te[mask]),
                        "category_only_roc_auc": float(roc_auc_score(
                            y_te[mask], rate_baseline(d_tr, d_te, ["c0"], base_rate)[mask]))}
    exposure["test_by_view_stratum"] = strata
    exposure["spearman_prediction_vs_views_test"] = float(pd.Series(p_te).corr(pd.Series(v_te), method="spearman"))
    seen = set(d_tr["title_key"])
    assert not d_te["title_key"].isin(seen).any()

    price = {"model_counterfactual_test": price_effect_model(d_te, pre, clf, B_te, MAIN),
             "raw_train": price_direction_raw(d_tr, pre)}
    off = sum(B_te[n].shape[1] for n in MAIN[:-1])
    num_coef = clf.coef_[0][off:off + 4]
    price["numeric_coefficients_standardized"] = dict(zip(
        ["log_price", "log_price_centered_sq", "relative_log_price", "relative_log_price_sq"], map(float, num_coef)))

    rule = {
        "auc_ge_0.65": bool(test["roc_auc"] >= SHIP_MIN_AUC),
        "gain_over_category_only_ge_0.03": bool(gain >= SHIP_MIN_GAIN_OVER_CATEGORY),
        "deciles_monotone": bool(monotone),
        "brier_better_than_base_rate": bool(test["brier"] < baselines["base_rate"]["brier"]),
    }
    ship = all(rule.values())

    # ---- 代わりの定義と感度分析(出荷判定には使わない)----
    alt = {}
    if not quick:
        alt["all_items_including_zero_views"] = quick_experiment(
            all_items.reset_index(drop=True), all_items["title_key"].to_numpy(), C, "alt:all items")
        one = df[df["views"] == 1].reset_index(drop=True)
        alt["single_view_items_only"] = quick_experiment(one, one["title_key"].to_numpy(), C, "alt:views==1")
        alt["split_by_product_id"] = quick_experiment(df, df["product_id"].to_numpy(), C, "alt:product split")
        alt["split_by_first_user"] = quick_experiment(df, df["first_user"].to_numpy(), C, "alt:user split")

    metrics = {
        "task": "P(listing gets >=1 item_like | listing was viewed >=1 time in the log)",
        "unit": "item_id", "label": "any item_like event for the item", "min_views": MIN_VIEWS,
        "features": ["title (TF-IDF word 1-2gram)", "category (top level)", "brand", "condition",
                     "log price, squared", "relative log price vs train median of category x brand, squared"],
        "model_name": f"logistic regression (L2, C={C})",
        "data": data,
        "split": {"method": "group split by lower-cased title", "seed": SEED,
                  "train": int(len(tr)), "validation": int(len(va)), "test": int(len(te)),
                  "positive_rate": {"train": base_rate, "validation": float(y_va.mean()), "test": float(y_te.mean())}},
        "base_rate": base_rate,
        "n_features": int(stack(B_te, MAIN).shape[1]),
        "candidates": candidates,
        "test": test,
        "baselines_test": baselines,
        "gain_over_category_only_auc": float(gain),
        "calibration_test": {"deciles": rel, "monotone": bool(monotone),
                             "brier_model": test["brier"], "brier_base_rate": baselines["base_rate"]["brier"],
                             "brier_skill": float(1 - test["brier"] / baselines["base_rate"]["brier"])},
        "variants_same_C": variants,
        "exposure": exposure,
        "price_effect": price,
        "alternatives": alt,
        "ship_rule": {"thresholds": {"min_auc": SHIP_MIN_AUC, "min_gain_over_category_only": SHIP_MIN_GAIN_OVER_CATEGORY},
                      "checks": rule, "ship": bool(ship)},
        "levels": {"high": f"probability >= base_rate * {LEVEL_HIGH}", "low": f"probability < base_rate * {LEVEL_LOW}",
                   "high_factor": LEVEL_HIGH, "low_factor": LEVEL_LOW,
                   "test_share": {"high": float((p_te >= base_rate * LEVEL_HIGH).mean()),
                                  "low": float((p_te < base_rate * LEVEL_LOW).mean())},
                   "test_observed_rate": {
                       "high": float(y_te[p_te >= base_rate * LEVEL_HIGH].mean()) if (p_te >= base_rate * LEVEL_HIGH).any() else None,
                       "mid": float(y_te[(p_te < base_rate * LEVEL_HIGH) & (p_te >= base_rate * LEVEL_LOW)].mean()),
                       "low": float(y_te[p_te < base_rate * LEVEL_LOW].mean()) if (p_te < base_rate * LEVEL_LOW).any() else None}},
        "versions": {"python": sys.version.split()[0], "scikit_learn": sklearn.__version__,
                     "numpy": np.__version__, "pandas": pd.__version__},
    }

    if ship:
        save(metrics, pre, clf, B_te, d_tr, d_te, p_te, base_rate)
    metrics["train_seconds"] = round(time.time() - t0, 1)
    text = json.dumps(metrics, ensure_ascii=False, indent=1)
    if ship:
        with open(os.path.join(MODEL_DIR, "like_metrics.json"), "w") as f:
            f.write(text)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
    print(text)
    log("SHIP" if ship else "NOT SHIPPED", rule, "total sec", metrics["train_seconds"])


def save(metrics, pre, clf, B_te, d_tr, d_te, p_te, base_rate) -> None:
    """出荷するときだけ呼ぶ。sklearn の分類器は保存せず、係数を辞書にして保存する(推論は numpy だけで足りる)。"""
    w = clf.coef_[0].astype(np.float64)
    sizes = [B_te[n].shape[1] for n in MAIN]
    o = np.cumsum([0] + sizes)
    part = {n: w[o[i]:o[i + 1]] for i, n in enumerate(MAIN)}
    cat = d_tr.groupby("c0")["y"].agg(["mean", "size"])
    art = {
        "version": 1,
        "vec": pre["vec"],
        "coef_title": part["title"].astype(np.float32),
        "coef_c0": {k: float(part["c0"][i]) for k, i in pre["c0"].items()},
        "coef_brand": {k: float(part["brand"][i]) for k, i in pre["brand"].items()},
        "coef_brand_other": float(part["brand"][len(pre["brand"])]),
        "coef_cond": {k: float(part["cond"][i]) for k, i in pre["cond"].items()},
        "coef_num": [float(v) for v in part["num"]],
        "num_mean": [float(v) for v in pre["num_mean"]], "num_std": [float(v) for v in pre["num_std"]],
        "lp_center": pre["lp_center"], "rel_clip": REL_CLIP,
        "intercept": float(clf.intercept_[0]),
        "med_overall": pre["med_overall"], "med_c0": pre["med_c0"], "med_c0_brand": pre["med_c0_brand"],
        "median_min_count": MEDIAN_MIN_COUNT,
        "base_rate": base_rate,
        "category_like_rate": {k: [float(r["mean"]), int(r["size"])] for k, r in cat.iterrows()},
        "level_high_factor": LEVEL_HIGH, "level_low_factor": LEVEL_LOW,
        "sklearn_version": sklearn.__version__,
    }
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, "like.joblib")
    joblib.dump(art, path, compress=3)
    size_mb = os.path.getsize(path) / 1e6
    if size_mb > MAX_ARTIFACT_MB:
        os.remove(path)
        raise RuntimeError(f"artifact {size_mb:.2f} MB > {MAX_ARTIFACT_MB} MB; lower TFIDF_MAX_FEATURES")
    # 推論コード(app/ml/attention.py)が学習時と同じ確率を出すか確かめるための見本(test から 25 件)
    idx = np.random.RandomState(SEED).choice(len(d_te), 25, replace=False)
    metrics["parity_examples"] = [
        {"title": d_te["title"].iloc[i], "category": d_te["c0"].iloc[i], "brand": d_te["brand"].iloc[i],
         "condition": d_te["condition"].iloc[i], "price_usd": float(d_te["price"].iloc[i]),
         "probability": float(p_te[i])} for i in idx]
    metrics["artifact"] = {"file": "like.joblib", "size_mb": round(size_mb, 2)}


if __name__ == "__main__":
    main()
