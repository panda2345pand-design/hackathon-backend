"""エントリポイント。ルーター(コントローラー層)を束ねるだけで、処理はここに書かない。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  テーブル定義を読み込む
from app.config import settings
from app.db import engine
from app.db_migrate import run_migrations
from app.errors import AppError
from app.routers import ai, health, items, users
from app.services import ai as ai_service

# app.* の INFO ログ(ウォームアップにかかった秒数など)を Cloud Run のログに出す。
# 何も設定しないと WARNING 以上しか出ない。ほかのライブラリは WARNING のままにして、ログを増やしすぎない
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)
logging.getLogger("alembic").setLevel(logging.INFO)  # どのマイグレーションを実行したかを残す


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations(engine)  # テーブルを最新の形にする(Alembic)。すでに最新なら何もしない。詳しくは app/db_migrate.py
    if settings.seed_on_empty:
        from scripts import seed
        seed.main()
    ai_service.warm_up()  # Gemini の1回目の遅さを先に済ませる(別スレッド。未設定なら何もしない)
    yield


app = FastAPI(title="Nego API", version="0.2.0", lifespan=lifespan,
              description="UTTC 10期 ハッカソン(AIコース)次世代フリマアプリのバックエンド")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, e: AppError):
    return JSONResponse(status_code=e.status, content={"detail": {"code": e.code, "message": e.message}})


for r in (health.router, users.router, items.router, ai.router):
    app.include_router(r)


@app.get("/")
def root():
    return {"message": "Hello World", "service": "nego-backend"}
