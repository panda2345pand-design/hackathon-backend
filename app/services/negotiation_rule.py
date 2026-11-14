"""AI 同士の価格交渉の「数字の規則」。DB も Gemini も使わない、ただの計算(同じ入力なら必ず同じ結果)。

なぜ規則にしたか:合意するか・いくらで合意するかを Gemini に決めさせると、同じ条件でも結果が変わり、
「なぜこの金額になったのか」を説明できない。お金に関わる部分はこの関数だけが決め、Gemini は各往復のせりふだけを書く。

規則(r = 1..4):
- 買い手は「出品価格の 70%」(上限のほうが低ければ上限)から始めて、上限(max_price)に向けて同じ幅ずつ上げる
- 売り手は出品価格から始めて、最低価格(floor)に向けて同じ幅ずつ下げる
- 買い手の提示が売り手の提示以上になった往復で合意。合意額は 2 つの中間
- 4 往復しても届かなければ不成立(上限が最低価格より低いときだけ起きる)
- 上限が出品価格以上なら、1 往復目に出品価格で合意(出品価格より高くは買わせない)
"""
import math

ROUNDS = 4            # 往復の回数
START_PERCENT = 70    # 買い手が最初に出す金額(出品価格に対する %)
UNIT = 100            # 金額を丸める単位(円)
AGREED, NO_DEAL = "agreed", "no_deal"


def _round_to_unit(x: float) -> int:
    """100 円単位に四捨五入する(50 円ちょうどは切り上げ)。
    Python の round() は「偶数への丸め」で 4550 → 4500 と 4650 → 4700 が混ざるので使わない。"""
    return math.floor(x / UNIT + 0.5) * UNIT


def _clamp(x: int, low: int, high: int) -> int:
    return max(low, min(high, x))


def negotiate(price: int, floor: int, max_price: int) -> dict:
    """交渉を最後まで進める。price = 出品価格、floor = 売り手の最低価格、max_price = 買い手の上限。
    返り値:{"outcome": "agreed" | "no_deal", "agreed_price": int | None,
            "rounds": [{"round": 1, "buyer_offer": int, "seller_ask": int}, ...]}
    合意したら、その往復で rounds は終わる。"""
    if max_price >= price:  # 定価で買える人には定価で売る。交渉で定価より高くなることはない
        return {"outcome": AGREED, "agreed_price": price,
                "rounds": [{"round": 1, "buyer_offer": price, "seller_ask": price}]}

    start = min(max_price, price * START_PERCENT // 100)
    rounds = []
    for r in range(1, ROUNDS + 1):
        if r == ROUNDS:
            # 最後の往復は、お互いの限度額そのものを出す(丸めない)。
            # 丸めると「上限 5,040 円・最低価格 5,020 円」のように届いているのに不成立になる組み合わせが出るため
            buyer_offer, seller_ask = max_price, floor
        else:
            # 丸めた結果が自分の限度を越えないように収める(買い手は上限より高く出さない。売り手は最低価格より下げない)
            buyer_offer = _clamp(_round_to_unit(start + (max_price - start) * r / ROUNDS), start, max_price)
            seller_ask = _clamp(_round_to_unit(price - (price - floor) * r / ROUNDS), floor, price)
        rounds.append({"round": r, "buyer_offer": buyer_offer, "seller_ask": seller_ask})
        if buyer_offer >= seller_ask:
            middle = _round_to_unit((buyer_offer + seller_ask) / 2)
            return {"outcome": AGREED, "agreed_price": _clamp(middle, floor, max_price), "rounds": rounds}
    return {"outcome": NO_DEAL, "agreed_price": None, "rounds": rounds}


def rule_text(outcome: str) -> str:
    """合意の決め方を画面に出すための文。金額(とくに相手の限度額)は入れない。"""
    base = (f"買い手のAIは上限に向けて、売り手のAIは最低価格に向けて、{ROUNDS}往復で同じ幅ずつ歩み寄ります。"
            f"買い手の提示が売り手の提示以上になった往復で、2つの中間({UNIT}円単位)を合意額にします。")
    if outcome == NO_DEAL:
        return base + f"今回は{ROUNDS}往復しても届かなかったので不成立です。"
    return base
