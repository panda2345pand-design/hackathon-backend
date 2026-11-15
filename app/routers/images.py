from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app import schemas as S
from app.auth import current_user
from app.config import settings
from app.db import get_db
from app.models.entities import User
from app.services import images as images_service

router = APIRouter(tags=["images"])


def _image_url(request: Request, image_id: str) -> str:
    """写真の絶対 URL。PUBLIC_BASE_URL があればそれを使う(Cloud Run はプロキシの後ろにいて、
    リクエストから読める URL が http:// になることがあるため)。無ければ、リクエストが届いた URL。"""
    base = settings.public_base_url or str(request.base_url)
    return f"{base.rstrip('/')}/images/{image_id}"


@router.post("/images", response_model=S.ImageOut, status_code=201)
def upload_image(request: Request, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    # 上限より 1 バイトだけ多く読む:大きすぎるかどうかは分かり、巨大なファイルを全部メモリに載せることはない
    data = file.file.read(images_service.MAX_BYTES + 1)
    image = images_service.save(db, user, data)
    return S.ImageOut(id=image.id, url=_image_url(request, image.id), content_type=image.content_type)


@router.get("/images/{image_id}")
def get_image(image_id: str, db: Session = Depends(get_db)):
    image = images_service.get(db, image_id)
    # immutable:同じ id の中身は変わらない(差し替えは新しい id になる)ので、ブラウザは 1 年間問い合わせずに使い回してよい
    # nosniff:ブラウザに中身から種類を推測させない(保存してある Content-Type のとおりに扱わせる)
    return Response(content=image.data, media_type=image.content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})
