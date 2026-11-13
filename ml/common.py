"""学習スクリプト共通: MerRec の読み込み・商品単位への重複排除・分割。

MerRec (Mercari US, CC BY-NC 4.0) https://huggingface.co/datasets/mercari-us/merrec
1 行 = 1 つの行動イベント(閲覧・いいね等)なので、item_id で 1 商品 1 行にまとめてから使う。
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MODEL_DIR = os.path.join(ROOT, "models_ml")
SEED = 42

RAW_COLUMNS = [
    "item_id", "stime", "name", "price",
    "c0_name", "c1_name", "c2_name",
    "brand_name", "item_condition_id", "item_condition_name",
]
# 状態は item_condition_id 1..5 の順(良い → 悪い)
CONDITION_ORDER = ["New", "Like new", "Good", "Fair", "Poor"]


def load_items(data_dir: str = DATA_DIR) -> tuple[pd.DataFrame, dict]:
    """全シャードを読み、item_id ごとに最新イベントの 1 行だけ残す。"""
    files = sorted(glob.glob(os.path.join(data_dir, "merrec*.parquet")))
    if not files:
        raise FileNotFoundError(f"no merrec*.parquet in {data_dir}")
    raw = pd.concat([pd.read_parquet(f, columns=RAW_COLUMNS) for f in files], ignore_index=True)
    stats = {
        "files": [os.path.basename(f) for f in files],
        "event_rows": int(len(raw)),
        "date_min": str(raw["stime"].min()),
        "date_max": str(raw["stime"].max()),
    }
    items = raw.sort_values("stime").drop_duplicates("item_id", keep="last")
    stats["unique_items"] = int(len(items))

    ok = (
        items["item_condition_name"].isin(CONDITION_ORDER)
        & items["price"].gt(0)
        & items["name"].notna()
        & items["c0_name"].notna()
    )
    stats["dropped_invalid"] = int((~ok).sum())
    items = items[ok]
    stats["items_used"] = int(len(items))

    df = pd.DataFrame({
        "item_id": items["item_id"].to_numpy(),
        "title": items["name"].astype(str).to_numpy(),
        "c0": items["c0_name"].astype(str).to_numpy(),
        "c1": items["c1_name"].fillna("").astype(str).to_numpy(),
        "brand": items["brand_name"].fillna("").astype(str).to_numpy(),
        "condition": items["item_condition_name"].astype(str).to_numpy(),
        "price": items["price"].astype(float).to_numpy(),
    })
    # 並び順を item_id で固定(再現性のため)
    df = df.sort_values("item_id").reset_index(drop=True)
    stats["brand_missing_rate"] = float((df["brand"] == "").mean())
    return df, stats


def split_indices(n: int, seed: int = SEED, val: float = 0.1, test: float = 0.1):
    """商品単位のランダム分割(train 80% / validation 10% / test 10%)。"""
    perm = np.random.RandomState(seed).permutation(n)
    n_test = int(round(n * test))
    n_val = int(round(n * val))
    return perm[n_test + n_val:], perm[n_test:n_test + n_val], perm[:n_test]


def norm_brand(b) -> str:
    """ブランド名の正規化(app/ml/predictor.py と同じ規則: 前後空白を除き小文字化)。"""
    return (b or "").strip().lower()


def price_features(df: pd.DataFrame, category_mode: str = "top") -> np.ndarray:
    """価格モデルの入力: object 配列 [title, category, brand, condition]。

    pandas を推論時に必要としないよう、列名ではなく列番号で扱う。
    """
    if category_mode == "top":
        cat = df["c0"].to_numpy()
    elif category_mode == "top/sub":
        cat = (df["c0"] + "/" + df["c1"]).to_numpy()
    else:
        raise ValueError(category_mode)
    X = np.empty((len(df), 4), dtype=object)
    X[:, 0] = df["title"].to_numpy()
    X[:, 1] = cat
    X[:, 2] = df["brand"].map(norm_brand).to_numpy()
    X[:, 3] = df["condition"].to_numpy()
    return X


def cluster_text(title: str, category: str) -> str:
    """クラスタリングの入力文字列(app/ml/predictor.py と同じ規則)。"""
    return f"{title or ''} {category or ''}".strip()
