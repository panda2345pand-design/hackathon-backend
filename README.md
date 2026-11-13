# Nego — backend(UTTC 10期 ハッカソン / AIコース)

「次世代フリマアプリ」のバックエンド。FastAPI(Python)+ SQLAlchemy。本番は Cloud Run + Cloud SQL(MySQL)。
フロントエンドは別リポジトリ [hackathon-frontend](https://github.com/panda2345pand-design/hackathon-frontend)(React / Vercel)。

## 構成(レイヤードアーキテクチャ・4層)

```
app/
  main.py          エントリポイント(ルーターを束ねるだけ)
  config.py        設定(環境変数)
  db.py            DB 接続(SQLAlchemy)
  routers/         ① コントローラー層:HTTP ↔ JSON。処理は書かない
  services/        ② ユースケース層:出品する・購入する・説明文を生成する…
  models/          ③ モデル層:User / Item / Order。ID と作成日時はここの生成関数で作る
  repositories/    ④ リポジトリ(DAO)層:モデル ↔ SQL
  schemas/         リクエスト/レスポンスの形(pydantic)
tests/             pytest
```

### ポリシー(先に決めておくこと)
- **ID は UUID。モデルの生成関数(`new_user()` / `new_item()` / `new_order()`)が作る。** フロントエンドから ID・作成日時・確定金額は受け取らない。
- 層をまたいで飛ばさない(routers → services → repositories → DB)。
- 外部 API(Gemini)・学習済みモデルの呼び出しは services に置く。

## 機能と API
仕様は [docs/api.md](docs/api.md)。起動後は `/docs`(Swagger UI)でも見られる。

| 機能 | 主なエンドポイント | 実装の場所 |
|---|---|---|
| 登録・認証 | `POST /auth/register`, `GET /me` | `app/auth.py`, `services/users.py` |
| 出品(禁止物チェック → 価格予測・クラスタ付与 → 保存) | `POST /items` | `services/items.py` |
| 一覧・検索・詳細 | `GET /items`, `GET /items/{id}` | `repositories/items.py` |
| 購入(同時購入は1人だけ成功) | `POST /items/{id}/purchase` | `services/items.py` |
| 価格交渉 | `POST /items/{id}/offers`, `POST /offers/{id}/accept` | `services/offers.py` |
| 教師あり学習:価格予測 | `GET /items/{id}/price-estimate`, `POST /ai/price` | `app/ml/predictor.py`, `ml/train_price.py` |
| 教師なし学習:クラスタリング | `GET /clusters`, `GET /items/{id}/similar` | `app/ml/predictor.py`, `ml/train_cluster.py` |
| Gemini:説明文・禁止物の審査・交渉の文面 | `POST /ai/describe`, `/ai/check`, `/ai/negotiate` | `services/ai.py` |

## 認証の2つのモード(`AUTH_MODE`)
- `dev`(既定):`Authorization: Bearer dev:<uid>` をそのまま信じる。ローカルとテスト用。
- `firebase`(本番):Firebase の ID トークンを Google の公開鍵で署名検証する。**本番では必ずこちら。**

## 機械学習
- データ:MerRec(Mercari US, CC BY-NC 4.0)2023年5月分の4ファイル。出典は [seed/README.md](seed/README.md)。
- 価格予測:Ridge 回帰(タイトルの TF-IDF + カテゴリ・ブランド・状態)。テスト 141,614 商品で MAE 34.13 USD(カテゴリ別中央値のベースラインは 45.30 USD)、R²(対数)0.510。
- クラスタリング:TF-IDF → SVD 50次元 → KMeans(k=38、シルエット係数 0.356)。
- 数字の出どころは `models_ml/price_metrics.json`・`models_ml/cluster_info.json`。学習し直しは `python -m ml.train_price` / `python -m ml.train_cluster`(要 `pip install -r ml/requirements-train.txt` と `data/` のデータ)。
- 限界:米国・英語・ドルのデータを 1 USD = 150 円の固定で換算。日本語のタイトルは価格の推定に使われない。

## デモデータ
```bash
python -m scripts.seed          # 商品が無いときだけ 300 件入れる
python -m scripts.seed --reset  # デモ用ユーザーの商品を入れ直す
```

## 動かし方(ローカル)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs
pytest
```

## デプロイ(Cloud Run)

```bash
gcloud run deploy nego-backend --source . --region asia-northeast1 --allow-unauthenticated
```
環境変数(DATABASE_URL, GEMINI_API_KEY など)は Cloud Run のサービス設定で渡す。`.env` はコミットしない。
