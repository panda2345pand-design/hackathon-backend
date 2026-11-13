"""DB 接続(SQLAlchemy)。ローカルは SQLite、本番は Cloud SQL(MySQL)。
テーブル定義は app/models/ に置き、接続の作り方だけをここに置く(リポジトリ層が使う)。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI の Depends で使う。1リクエスト1セッション。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
