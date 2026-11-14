# Nego API 仕様(契約)v1 — 2026-11-14

ベース URL:ローカル `http://localhost:8000`。JSON。日時は ISO 8601。金額は **円(整数)**。
認証:`Authorization: Bearer <token>`。
- 本番(AUTH_MODE=firebase):token = Firebase ID トークン
- 開発(AUTH_MODE=dev):token = `dev:<任意のuid>`(例 `dev:alice`)。テストとローカル用
エラー:`{"detail": {"code": "<機械向けコード>", "message": "<日本語>"}}`。401 未ログイン / 403 権限なし / 404 なし / 409 状態の衝突 / 422 入力不正・出品禁止。

## 型
- User: `{id, name, created_at}`
- ItemSummary: `{id, title, price, category, condition, image_url, status, cluster_id, like_count, created_at}`(status = `on_sale` | `sold`)
- ItemDetail: ItemSummary + `{description, brand, predicted_price, cluster_label, cluster_label_ja, seller: {id, name}, liked}`(liked は未ログインなら false。cluster_label_ja は 2026-11-15 追加)
- Order: `{id, item_id, item_title, price, created_at}`
- Offer: `{id, item_id, item_title, buyer: {id, name}, amount, message, ai_comment, status, created_at}`(status = `pending` | `accepted` | `rejected`)
- PriceEstimate: `{predicted_price, low, high, currency: "JPY", basis: [{feature, value, effect}], model: {name, mae, r2, trained_rows}, available}`(モデルが無いとき available=false で数値は null)

## エンドポイント
| メソッド | パス | 認証 | 入力 | 出力 |
|---|---|---|---|---|
| GET | /health | — | | `{status:"ok"}` |
| POST | /auth/register | ✔ | `{name}` | User(既にあれば名前を更新して返す) |
| GET | /me | ✔ | | User(未登録は 404 code=not_registered) |
| GET | /categories | — | | `{categories:[str], conditions:[str]}` |
| GET | /clusters | — | | `{clusters:[{id,label,label_ja,count}]}`(ホームのチップ用。中身が寄せ集めのクラスタは出さない。下の「クラスタの名前」) |
| GET | /items | 任意 | query: q, category, cluster, status(既定 on_sale), limit(既定 24, 最大 100), offset | `{items:[ItemSummary], total}` |
| GET | /items/{id} | 任意 | | ItemDetail |
| POST | /items | ✔ | `{title, description, category, brand?, condition, price, image_url?}` | 201 ItemDetail。禁止物なら 422 code=prohibited |
| POST | /items/{id}/purchase | ✔ | | 201 Order。自分の商品 403 code=own_item、売り切れ 409 code=sold |
| GET | /items/{id}/price-estimate | — | | PriceEstimate |
| GET | /items/{id}/similar | — | query: limit(既定 8) | `{cluster_id, cluster_label, cluster_label_ja, items:[ItemSummary]}` |
| POST | /items/{id}/like | ✔ | | `{like_count, liked:true}` |
| DELETE | /items/{id}/like | ✔ | | `{like_count, liked:false}` |
| POST | /items/{id}/offers | ✔ | `{amount, message?}` | 201 Offer(自分の商品は 403、sold は 409) |
| GET | /items/{id}/offers | ✔(出品者のみ) | | `{offers:[Offer]}` |
| POST | /offers/{id}/accept | ✔(出品者のみ) | | Offer(accepted。同じ商品のほかの pending は rejected) |
| POST | /offers/{id}/reject | ✔(出品者のみ) | | Offer |
| GET | /me/items | ✔ | | `{items:[ItemSummary]}` |
| GET | /me/orders | ✔ | | `{orders:[Order]}` |
| GET | /me/offers | ✔ | | `{offers:[Offer]}`(自分が出した提案) |
| POST | /ai/describe | ✔ | `{title, category, condition, brand?, keywords?}` | `{description, source}`(source = `gemini` | `fallback`) |
| POST | /ai/price | ✔ | `{title, category, brand?, condition}` | PriceEstimate |
| POST | /ai/check | ✔ | `{title, description, category}` | `{ok, reason, source}` |
| POST | /ai/negotiate | ✔ | `{item_id, amount}` | `{acceptance: "high"|"mid"|"low", reply, seller_tip, predicted_price, source}` |

## クラスタの名前(2026-11-15 追加。既存の項目は変えていない)
- `label` / `cluster_label`:学習時に自動で付いた英語のラベル(例 `Men: nike / air / max`)。今までどおり。
- `label_ja` / `cluster_label_ja`:画面に出す短い日本語名(12字以内。例 `ナイキ スニーカー`)。手作業で付けた対応表 `models_ml/cluster_labels_ja.json` から引く。**画面にはこちらを出す。**
  - 日本語名が無いクラスタは英語のラベルと同じ値が入る。クラスタが付いていない商品(`cluster_id` が null)は `cluster_label_ja` も null。
- 出る場所:`GET /clusters` の各要素の `label_ja`/`GET /items/{id}`・`POST /items` の `cluster_label_ja`/`GET /items/{id}/similar` の `cluster_label_ja`/`GET /ai/info` の `ml.cluster.clusters[]` の `label_ja` と `hidden`。
- 隠すクラスタ:中身が寄せ集めのクラスタ(対応表で `hidden: true`。今は 23・28・31 の3つ。理由は対応表に書いてある)は `GET /clusters` に出さない。
  隠すのは一覧だけ。その商品の詳細の `cluster_id`/`cluster_label_ja`、`GET /items/{id}/similar`、`GET /items?cluster=<id>` は今までどおり動く。

## 決まり
- 購入価格はサーバーが決める:購入者に accepted の Offer があればその amount、なければ item.price。
- ID(UUID)・created_at はサーバーのモデル生成関数が作る。クライアントから受け取らない。
- `source: "fallback"` は Gemini のキーが無い/失敗したときの決め打ち応答(画面に「AI 未接続」と出す)。
