"""エントリポイント。ルーター(コントローラー層)を束ねるだけで、処理はここに書かない。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  テーブル定義を読み込む
from app.config import settings
from app.db import Base, engine
from app.errors import AppError
from app.routers import ai, health, items, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # 無いテーブルだけ作る(列の変更は追わない。本格運用なら Alembic)
    if settings.seed_on_empty:
        from scripts import seed
        seed.main()
    yield


app = FastAPI(title="Nego API", version="0.2.0", lifespan=lifespan,
              description="UTTC 10期 ハッカソン(AIコース)次世代フリマアプリのバックエンド")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
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
