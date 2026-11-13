"""エントリポイント。ルーター(コントローラー層)を束ねるだけで、処理はここに書かない。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import health

app = FastAPI(title="Nego API", version="0.1.0", description="UTTC 10期 ハッカソン(AIコース)次世代フリマアプリのバックエンド")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)


@app.get("/")
def root():
    return {"message": "Hello World", "service": "nego-backend"}
