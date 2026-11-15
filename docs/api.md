# Nego API 仕様(契約)v1 — 2026-11-14(2026-11-15:AI 同士の価格交渉・写真・お知らせを追加)

ベース URL:ローカル `http://localhost:8000`。JSON。日時は ISO 8601。金額は **円(整数)**。
認証:`Authorization: Bearer <token>`。
- 本番(AUTH_MODE=firebase):token = Firebase ID トークン
- 開発(AUTH_MODE=dev):token = `dev:<任意のuid>`(例 `dev:alice`)。テストとローカル用
エラー:`{"detail": {"code": "<機械向けコード>", "message": "<日本語>"}}`。401 未ログイン / 403 権限なし / 404 なし / 409 状態の衝突 / 413 大きすぎ / 415 対応していない種類 / 422 入力不正・出品禁止 / 429 回数の上限。
入力の形そのものが違うとき(必須の項目が無い・型が違う・範囲の外)は FastAPI の標準の 422(`detail` が配列)。上の形になるのは、この文書に code を書いてあるエラー。

## 型
- User: `{id, name, created_at}`
- ItemSummary: `{id, title, price, category, condition, image_url, status, cluster_id, like_count, created_at, auto_negotiation, image_ai_generated}`(status = `on_sale` | `sold`)
  - `auto_negotiation: bool` … 売り手が最低価格を設定していて販売中なら true(だれにでも見える。金額は出ない)
  - `image_ai_generated: bool` … 画像が、見本データ用に AI が作ったイメージなら true(画面で「AI 生成のイメージ」と明記するため)
- ItemDetail: ItemSummary + `{description, brand, predicted_price, cluster_label, cluster_label_ja, seller: {id, name}, liked, min_price}`(liked は未ログインなら false。cluster_label_ja は 2026-11-15 追加)
  - `min_price: int | null` … **出品者本人が認証つきで読んだときだけ値が入る。それ以外(ほかの人・未ログイン・正しくないトークン)は必ず null**
- Order: `{id, item_id, item_title, price, created_at}`
- Offer: `{id, item_id, item_title, buyer: {id, name}, amount, message, ai_comment, status, created_at}`(status = `pending` | `accepted` | `rejected`)
- Negotiation / Image / Notification: 下の各節
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
| POST | /items | ✔ | `{title, description, category, brand?, condition, price, image_url?, min_price?}` | 201 ItemDetail。禁止物なら 422 code=prohibited。min_price が 1 未満か price より大きいと 422 code=invalid_min_price |
| PATCH | /items/{id}/min-price | ✔(出品者のみ) | `{min_price: int \| null}`(必須。null = 自動交渉をやめる) | ItemDetail。他人は 403 code=not_seller、範囲の外は 422 code=invalid_min_price |
| POST | /items/{id}/auto-negotiate | ✔ | `{max_price}`(1〜9,999,999) | Negotiation(下の「AI 同士の価格交渉」) |
| GET | /items/{id}/negotiations | ✔ | | `{negotiations:[Negotiation]}` 新しい順 |
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
| POST | /images | ✔ | multipart/form-data、フィールド名 `file` | 201 `{id, url, content_type}`(下の「写真」) |
| GET | /images/{id} | — | | 画像のバイト列。無ければ 404 code=image_not_found |
| POST | /ai/describe-image | ✔ | `{image_id}` | `{title, category, condition, brand, description, source}`(下の「写真」) |
| POST | /ai/attention | — | `{title, category, condition, brand?, price}` | いつも `{available: false}`(下の「いいねされやすさ」) |
| GET | /ai/info | — | | `{gemini: {connected, model}, ml: {…, attention: {available: false, reason}}}` |
| GET | /me/notifications | ✔ | query: limit(既定 20、1〜100) | `{unread_count, notifications:[Notification]}` 新しい順 |
| POST | /me/notifications/read-all | ✔ | | `{unread_count: 0}` |

## クラスタの名前(2026-11-15 追加。既存の項目は変えていない)
- `label` / `cluster_label`:学習時に自動で付いた英語のラベル(例 `Men: nike / air / max`)。今までどおり。
- `label_ja` / `cluster_label_ja`:画面に出す短い日本語名(12字以内。例 `ナイキ スニーカー`)。手作業で付けた対応表 `models_ml/cluster_labels_ja.json` から引く。**画面にはこちらを出す。**
  - 日本語名が無いクラスタは英語のラベルと同じ値が入る。クラスタが付いていない商品(`cluster_id` が null)は `cluster_label_ja` も null。
- 出る場所:`GET /clusters` の各要素の `label_ja`/`GET /items/{id}`・`POST /items` の `cluster_label_ja`/`GET /items/{id}/similar` の `cluster_label_ja`/`GET /ai/info` の `ml.cluster.clusters[]` の `label_ja` と `hidden`。
- 隠すクラスタ:中身が寄せ集めのクラスタ(対応表で `hidden: true`。今は 23・28・31 の3つ。理由は対応表に書いてある)は `GET /clusters` に出さない。
  隠すのは一覧だけ。その商品の詳細の `cluster_id`/`cluster_label_ja`、`GET /items/{id}/similar`、`GET /items?cluster=<id>` は今までどおり動く。

## AI 同士の価格交渉(2026-11-15 追加)

売り手は「ここまでなら下げてよい」最低価格(`min_price`)を、買い手は「ここまでなら出せる」上限(`max_price`)を、それぞれ自分の AI にだけ伝える。
2つの AI が最大4往復して、合意額を出す。

**合意するか・いくらかは数字の規則で決める(`app/services/negotiation_rule.py`。説明できるように Gemini には任せない)。Gemini は各往復のせりふだけを書く。**

規則(r = 1..4):
- 売り手の最低価格 `floor` = item.min_price
- 買い手の出発点 `start` = min(max_price, price の 70%(1 円未満は切り捨て))
- 買い手の提示 `buyer_offer[r]` = start + (max_price − start) × r/4
- 売り手の提示 `seller_ask[r]` = price − (price − floor) × r/4
- 1〜3 往復目の金額は 100 円単位に四捨五入する(50 円ちょうどは切り上げ)。丸めた結果が自分の限度を越えるときは限度に収める。
  **4 往復目は丸めず、限度額そのもの**(buyer_offer = max_price、seller_ask = floor)を出す。丸めると、届いているのに不成立になる組み合わせがあるため
- はじめて `buyer_offer[r] >= seller_ask[r]` になった往復で合意。合意額 = 2つの中間(100 円単位に四捨五入してから、floor 以上 max_price 以下に収める。
  このため、限度額が 100 円単位でないときは合意額も 100 円単位でないことがある)。`rounds` は合意した往復で終わる
- 4往復しても届かなければ不成立(= max_price < floor のとき。そのときだけ)
- max_price >= price のときは1往復目で定価で合意(`rounds` は 1 つ。buyer_offer = seller_ask = price。定価より高くは買わせない)

### `POST /items/{id}/auto-negotiate`(認証必須・買い手)
入力 `{"max_price": int}`(1 以上 9,999,999 以下。範囲の外は標準の 422)

返り値 200(Negotiation):
```json
{
  "id": "交渉記録の UUID",
  "item_id": "…",
  "outcome": "agreed",
  "agreed_price": 5800,
  "list_price": 6500,
  "max_price": 6000,
  "rounds": [
    {"round": 1, "buyer_offer": 4900, "buyer_message": "…", "seller_ask": 6300, "seller_message": "…"}
  ],
  "offer_id": "…",
  "source": "gemini",
  "rule": "合意の決め方(日本語の文)",
  "created_at": "…",
  "buyer": {"id": "…", "name": "…"}
}
```
- `outcome`: `agreed` | `no_deal`。`agreed_price` と `offer_id` は no_deal なら null
- `max_price`: 買い手本人への返り値にだけ入る(出品者が読むと null)
- `source`: せりふの出どころ。`gemini` | `fallback`(Gemini が使えない・返事の形が違う・せりふの金額が違うときの定型文)
- `buyer`: 契約の案では一覧(GET)だけにあったが、POST の返り値にも入れている(同じ形にするため)
- 合意したら、同じトランザクションで、既存の offers に `status=accepted, amount=agreed_price` の行を作る(`offer_id`)→ 既存の購入(`POST /items/{id}/purchase`)が合意額で通る。出品者には `auto_negotiation_agreed` のお知らせが届く
- エラー(上から順に確かめる): 商品なし 404 `item_not_found` / 自分の商品 403 `own_item` / 売り切れ 409 `sold_out` / 売り手が最低価格を設定していない 409 `auto_negotiation_disabled` /
  この買い手がこの商品ですでに承諾済みの交渉を持っている(出品者が手で承諾したものも含む)409 `already_agreed` / 回数の上限 429 `too_many_negotiations`
- 不成立のあと、同じ買い手は上限を変えてやり直せる。1 商品につき 1 人 5 回まで(成立・不成立を合わせて数える。6 回目が 429)
- せりふを待っている間に商品が売れたときは、合意を作らずに 409 `sold_out`
- 売り切れのコードは、この API だけ `sold_out`(購入・手動の交渉は今までどおり `sold`)

### `GET /items/{id}/negotiations`(認証必須)
- 出品者本人: この商品の全記録(`max_price` は null)
- それ以外: 自分が買い手の記録だけ(`max_price` つき)。記録が無ければ空の配列
- 返り値 `{"negotiations": [Negotiation]}` 新しい順

### 相手の限度額について(正直な注意)
- `min_price` という項目は出品者本人にしか返さない。`max_price` は買い手本人にしか返さない。Gemini にも「最低価格」「上限」としては渡さない。
- ただし **`rounds` の金額そのものから、相手の限度額は分かる**。4 往復目の `seller_ask` は最低価格そのもの、`buyer_offer` は上限そのもの(不成立のとき、4 往復目で合意したときは必ず出る)。
  それより前の往復でも、歩み寄りの幅が一定なので、規則を知っていれば逆算できる。これは上の規則から必ずそうなる性質で、実装で隠してはいない。

## 写真(2026-11-15 追加)

### `POST /images`(認証必須、multipart/form-data、フィールド名 `file`)
- JPEG / PNG / WebP、2MB(2,097,152 バイト)まで。それ以外 415 `unsupported_image`、大きすぎ 413 `image_too_large`
- 種類は**ファイルの中身(先頭のバイト列)**で決める。リクエストの Content-Type とファイル名は見ない。返り値の `content_type` は中身から決めた値
- 返り値 201 `{"id": "UUID", "url": "https://<バックエンド>/images/<id>", "content_type": "image/jpeg"}`
- `url` の先頭は環境変数 `PUBLIC_BASE_URL`。未設定なら、リクエストが届いた URL
- 出品時は、この `url` を今までどおり `image_url` に入れる

### `GET /images/{id}`(認証なし)
- 画像のバイト列。`Cache-Control: public, max-age=31536000, immutable`。無ければ 404 `image_not_found`

### `POST /ai/describe-image`(認証必須)
入力 `{"image_id": "UUID"}`(無い id は 404 `image_not_found`)

返り値 200:
```json
{"title": "…", "category": "Men", "condition": "Good", "brand": "Nike", "description": "…", "source": "gemini"}
```
- `category` と `condition` は `GET /categories` の値のどれか、または null(自信がないとき・選択肢に無い値が返ってきたとき)。`title`・`brand`・`description` も null あり
- Gemini が使えないときは 200 で `{"title": null, "category": null, "condition": null, "brand": null, "description": null, "source": "fallback"}`

### `image_ai_generated`
- 出品のとき、`image_url` の末尾が `/images/<id>` で、その写真の種類が `ai_generated` なら true。それ以外(写真なし・利用者の写真・外部の URL)は false
- `POST /images` で入る写真の種類はいつも `upload`。`ai_generated` の写真は、見本データを入れるスクリプトだけが作る

## お知らせ(アプリ内。2026-11-15 追加)

### `GET /me/notifications?limit=20`(認証必須)
```json
{"unread_count": 2, "notifications": [
  {"id": "…", "type": "offer_received", "message": "「…」に ¥5,800 の価格交渉が届きました", "item_id": "…", "read": false, "created_at": "…"}
]}
```
- type: `offer_received`(出品者へ)/ `offer_accepted` `offer_rejected`(買い手へ)/ `item_sold`(出品者へ)/ `auto_negotiation_agreed`(出品者へ。AI が代わりに合意したこと)
- `offer_rejected` は、出品者がほかの交渉を承諾して自動で断られた買い手にも届く
- 新しい順。`unread_count` は limit に関係なく未読の全体の数。message の商品名は 30 字で切る

### `POST /me/notifications/read-all`(認証必須)→ `{"unread_count": 0}`

## いいねされやすさ(2本目の教師あり学習。載せていない)
- モデルを検証した結果、精度が事前の基準に届かなかったので載せていない(test AUC 0.612 < 0.65。[ml/LIKE_REPORT.md](../ml/LIKE_REPORT.md))。
- `POST /ai/attention`(認証不要。入力 `{title, category, condition, brand?, price}`)は、いつも `{"available": false}` だけを返す。画面はこれを見て欄を出さない。
- `GET /ai/info` の `ml.attention` は `{"available": false, "reason": "…載せていない理由…"}`。

## 決まり
- 購入価格はサーバーが決める:購入者に accepted の Offer があればその amount、なければ item.price。
- ID(UUID)・created_at はサーバーのモデル生成関数が作る。クライアントから受け取らない。
- `source: "fallback"` は Gemini のキーが無い/失敗したときの決め打ち応答(画面に「AI 未接続」と出す)。
- AI 同士の価格交渉の合意額は、サーバーの規則が決める。クライアントが送るのは自分の限度額(min_price / max_price)だけ。
