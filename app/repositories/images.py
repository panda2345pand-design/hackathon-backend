from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Image


def add(db: Session, image: Image) -> Image:
    db.add(image)
    db.flush()
    return image


def get(db: Session, image_id: str) -> Image | None:
    return db.get(Image, image_id)


def kind_of(db: Session, image_id: str) -> str | None:
    """写真の種類だけを読む(バイト列は読まない。最大 2MB あるので、種類を知りたいだけのときに全部読むのは無駄)。"""
    return db.scalar(select(Image.kind).where(Image.id == image_id))
