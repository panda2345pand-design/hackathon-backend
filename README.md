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
