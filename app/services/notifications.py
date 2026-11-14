"""お知らせ(アプリ内)のユースケース:作る・読む・既読にする。

作る関数(notify)は commit しない。呼び出し側の出来事(交渉を出した・購入した…)と同じトランザクションで保存する。
なぜ:出来事だけ保存されてお知らせが無い、またはその逆、という食い違いを作らないため。
"""
from sqlalchemy.orm import Session

from app.models.entities import (AUTO_NEGOTIATION_AGREED, ITEM_SOLD, OFFER_ACCEPTED, OFFER_RECEIVED, OFFER_REJECTED,
                                 Item, Notification, User, new_notification)
from app.repositories import notifications as notifications_repo

TITLE_MAX_CHARS = 30  # お知らせに入れる商品名の長さ。長い商品名(最大 100 字)で文が読みにくくならないように切る

# 種類ごとの文。{title} = 商品名、{amount} = 金額(¥5,800 の形)
MESSAGES = {
    OFFER_RECEIVED: "「{title}」に {amount} の価格交渉が届きました",
    OFFER_ACCEPTED: "「{title}」の価格交渉({amount})が承諾されました。この金額で購入できます",
    OFFER_REJECTED: "「{title}」の価格交渉({amount})は見送られました",
    ITEM_SOLD: "「{title}」が {amount} で売れました",
    AUTO_NEGOTIATION_AGREED: "「{title}」は、AI があなたの代わりに交渉して {amount} で合意しました",
}


def short_title(title: str) -> str:
    return title if len(title) <= TITLE_MAX_CHARS else title[:TITLE_MAX_CHARS] + "…"


def message_for(type: str, title: str, amount: int) -> str:
    return MESSAGES[type].format(title=short_title(title), amount=f"¥{amount:,}")


def notify(db: Session, user_id: str, type: str, item: Item, amount: int) -> Notification:
    """お知らせを 1 件作る(commit は呼び出し側)。"""
    return notifications_repo.add(db, new_notification(user_id, type, message_for(type, item.title, amount), item.id))


def list_for(db: Session, user: User, limit: int) -> tuple[list[Notification], int]:
    """(新しい順のお知らせ, 未読の件数)。未読の件数は limit に関係なく全体の数。"""
    return notifications_repo.by_user(db, user.id, limit), notifications_repo.unread_count(db, user.id)


def read_all(db: Session, user: User) -> int:
    """自分のお知らせをすべて既読にする。返り値は残りの未読の件数(= 0)。"""
    notifications_repo.mark_all_read(db, user.id)
    db.commit()
    return notifications_repo.unread_count(db, user.id)
