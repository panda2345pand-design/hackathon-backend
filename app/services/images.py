"""写真のユースケース:保存する・取り出す・「AI 生成のイメージか」を調べる。"""
import re

from sqlalchemy.orm import Session

from app.errors import NotFound, PayloadTooLarge, UnsupportedMediaType
from app.models.entities import KIND_AI_GENERATED, KIND_UPLOAD, Image, User, new_image
from app.repositories import images as images_repo

MAX_BYTES = 2 * 1024 * 1024  # 2MB。DB に入れるので小さめにしている(画面側で縮小してから送る前提)

# 写真の URL の末尾(/images/<UUID>)。UUID は 36 文字
_IMAGE_URL = re.compile(r"/images/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")


def detect_content_type(data: bytes) -> str | None:
    """ファイルの先頭のバイト列(マジックナンバー)から画像の種類を決める。JPEG / PNG / WebP 以外は None。
    なぜ:リクエストの Content-Type やファイル名は送る側が自由に書けるので、信用しない。中身で決める。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save(db: Session, owner: User, data: bytes, kind: str = KIND_UPLOAD) -> Image:
    """写真を保存する。kind は API からは常に upload(ai_generated は見本データを入れるスクリプトだけが使う)。"""
    if len(data) > MAX_BYTES:
        raise PayloadTooLarge("写真は 2MB までです", "image_too_large")
    content_type = detect_content_type(data)
    if not content_type:
        raise UnsupportedMediaType("写真は JPEG / PNG / WebP のどれかにしてください", "unsupported_image")
    image = images_repo.add(db, new_image(owner.id, content_type, data, kind))
    db.commit()
    return image


def get(db: Session, image_id: str) -> Image:
    image = images_repo.get(db, image_id)
    if not image:
        raise NotFound("写真が見つかりません", "image_not_found")
    return image


def is_ai_generated(db: Session, image_url: str | None) -> bool:
    """image_url がこのアプリの写真(/images/<id>)を指していて、その種類が ai_generated なら True。
    外部の URL・写真なし・見つからない id は False。"""
    found = _IMAGE_URL.search((image_url or "").split("?")[0])
    return bool(found) and images_repo.kind_of(db, found.group(1)) == KIND_AI_GENERATED
