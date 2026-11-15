"""AI 同士の価格交渉のユースケース。

役割の分担:
- 合意するか・いくらか → 数字の規則(negotiation_rule.negotiate)。Gemini は関わらない
- 各往復のせりふ → Gemini(ai.negotiation_messages)。使えなければ定型文
- 記録・承諾済みの交渉(offers)・出品者へのお知らせ → 1 つのトランザクションで保存する

相手の限度額を見せない:売り手の最低価格(items.min_price)と買い手の上限(negotiations.max_price)は、
本人への返り値にしか入れない(app/schemas の to_detail / to_negotiation)。Gemini にも「限度額」としては渡さない。
売り手の提示は最低価格まで下げず、歩み寄る幅は毎回変える(negotiation_rule)。提示の金額から最低価格を逆算できないようにするため。
"""
import json

from sqlalchemy.orm import Session

from app.errors import Conflict, Forbidden, TooManyRequests
from app.models.entities import ACCEPTED, AUTO_NEGOTIATION_AGREED, SOLD, Item, Negotiation, User, new_negotiation, new_offer
from app.repositories import negotiations as negotiations_repo
from app.repositories import offers as offers_repo
from app.services import ai, negotiation_rule
from app.services import items as items_service
from app.services import notifications as notifications_service

MAX_PER_BUYER = 5  # 1 商品につき 1 人が交渉できる回数。上限を少しずつ変えて、売り手の最低価格を探り当てるのを難しくする
OFFER_MESSAGE = "AI 同士の価格交渉で合意しました"


def _item_open_for(db: Session, buyer: User, item_id: str) -> Item:
    """この買い手がこの商品で AI 同士の交渉を始められるかを確かめ、商品を返す。できなければ理由つきのエラー。"""
    item = items_service.get(db, item_id)
    if item.seller_id == buyer.id:
        raise Forbidden("自分の商品には交渉できません", "own_item")
    if item.status == SOLD:
        raise Conflict("この商品は売り切れです", "sold_out")
    if item.min_price is None:
        raise Conflict("この商品は AI 同士の価格交渉を受け付けていません", "auto_negotiation_disabled")
    if offers_repo.accepted_for(db, item.id, buyer.id):
        raise Conflict("この商品では、すでに合意した金額があります。その金額で購入できます", "already_agreed")
    if negotiations_repo.count_for(db, item.id, buyer.id) >= MAX_PER_BUYER:
        raise TooManyRequests(f"AI 同士の価格交渉は、1 つの商品につき {MAX_PER_BUYER} 回までです", "too_many_negotiations")
    return item


def run(db: Session, buyer: User, item_id: str, max_price: int) -> Negotiation:
    """AI 同士の価格交渉を 1 回行い、記録を返す。合意したら、承諾済みの交渉(offers)を作る → 既存の購入がその金額で通る。"""
    item = _item_open_for(db, buyer, item_id)
    title, list_price = item.title, item.price
    # 歩み寄る幅を決める乱数の種。商品・買い手・何回目か、から作る(毎回ちがう幅になる。あとから同じ計算をやり直すこともできる)
    attempt = negotiations_repo.count_for(db, item.id, buyer.id) + 1
    result = negotiation_rule.negotiate(list_price, item.min_price, max_price, seed=f"{item.id}:{buyer.id}:{attempt}")

    # ここまでは読み取りだけ。Gemini の返事を待つ数秒のあいだ DB のトランザクションを開けたままにしないよう、いったん閉じる。
    # 開けたままだと、待っている間に商品が売れても気づけない(MySQL は、トランザクションを始めた時点の内容を見せ続ける)
    db.rollback()
    messages, source = ai.negotiation_messages(title, result["rounds"], result["outcome"], result["agreed_price"])

    item = _item_open_for(db, buyer, item_id)  # 待っている間に売れた・ほかの画面で合意した、を確かめ直す
    rounds = [{**r, **m} for r, m in zip(result["rounds"], messages)]
    offer_id = None
    if result["outcome"] == negotiation_rule.AGREED:
        offer = offers_repo.add(db, new_offer(item.id, buyer.id, result["agreed_price"], OFFER_MESSAGE,
                                              negotiation_rule.rule_text(), status=ACCEPTED))
        offer_id = offer.id
        notifications_service.notify(db, item.seller_id, AUTO_NEGOTIATION_AGREED, item, result["agreed_price"])
    negotiation = negotiations_repo.add(db, new_negotiation(
        item.id, buyer.id, max_price, list_price, result["outcome"], result["agreed_price"],
        json.dumps(rounds, ensure_ascii=False), offer_id, source))
    db.commit()  # 記録・承諾済みの交渉・お知らせを、まとめて保存する(どれか 1 つだけ残ることがない)
    return negotiation


def list_for_item(db: Session, viewer: User, item_id: str) -> list[Negotiation]:
    """出品者本人には、この商品の全記録。それ以外の人には、自分が買い手の記録だけ。"""
    item = items_service.get(db, item_id)
    if item.seller_id == viewer.id:
        return negotiations_repo.by_item(db, item.id)
    return negotiations_repo.by_item(db, item.id, viewer.id)
