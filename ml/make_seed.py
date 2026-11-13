"""デモ用の初期データ seed/seed_items.json を作る(MerRec の実在商品 300 件)。

    .venv/bin/python -m ml.make_seed

- 価格モデルの test 分割(学習に使っていない商品)からだけ選ぶ
- 各トップカテゴリに 10 件ずつ、残りは件数に比例して配分(カテゴリが偏らないように)
- 説明文は項目から機械的に組み立てた 1 文だけ(作り話は入れない)
- 価格は USD × USD_JPY(固定レート)を 100 円単位に丸めたもの
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

from app.ml.predictor import CATEGORY_LABELS_JA, CONDITION_LABELS_JA, USD_JPY, _to_jpy
from ml.common import ROOT, SEED, load_items, split_indices

N_ITEMS = 300
MIN_PER_CATEGORY = 10
PRICE_RANGE_USD = (3, 2000)
TITLE_LEN = (12, 70)
# デモ画面に出したくない語を含む商品は選ばない(単純な語の一致)
BLOCK = re.compile(r"\b(sex\w*|adult|nude|lingerie|thong|vape|gun|knife|weed|cbd|reserved|bundle for|do not buy|hold for)\b", re.I)


def main() -> None:
    df, _ = load_items()
    _, _, te = split_indices(len(df))
    d = df.iloc[te]
    ok = (
        d["price"].between(*PRICE_RANGE_USD)
        & d["title"].str.len().between(*TITLE_LEN)
        & d["title"].map(lambda t: t.isascii() and not BLOCK.search(t))
    )
    d = d[ok].drop_duplicates("title")

    counts = d["c0"].value_counts()
    rest = N_ITEMS - MIN_PER_CATEGORY * len(counts)
    exact = counts / counts.sum() * rest
    quota = np.floor(exact).astype(int)
    for c in (exact - quota).sort_values(ascending=False).index[: rest - quota.sum()]:
        quota[c] += 1
    quota += MIN_PER_CATEGORY

    items = []
    for cat in counts.index:
        for _, r in d[d["c0"] == cat].sample(n=int(quota[cat]), random_state=SEED).iterrows():
            brand = r["brand"] or None
            head = f"{brand}の「{r['title']}」" if brand else f"「{r['title']}」"
            items.append({
                "title": r["title"],
                "category": cat,
                "brand": brand,
                "condition": r["condition"],
                "price_jpy": _to_jpy(r["price"]),
                "description": f"{head}。カテゴリ:{CATEGORY_LABELS_JA[cat]}。状態:{CONDITION_LABELS_JA[r['condition']]}。",
                "source": {"dataset": "MerRec", "item_id": int(r["item_id"]), "price_usd": float(r["price"])},
            })
    assert len(items) == N_ITEMS, len(items)
    out = os.path.join(ROOT, "seed", "seed_items.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print("saved", out, len(items), "USD_JPY", USD_JPY)
    print(quota.to_dict())


if __name__ == "__main__":
    main()
