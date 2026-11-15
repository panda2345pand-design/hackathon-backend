"""AI 同士の価格交渉の「数字の規則」。DB も Gemini も使わない、ただの計算(同じ入力・同じ seed なら必ず同じ結果)。

なぜ規則にしたか:合意するか・いくらで合意するかを Gemini に決めさせると、同じ条件でも結果が変わり、
「なぜこの金額になったのか」を説明できない。お金に関わる部分はこの関数だけが決め、Gemini は各往復のせりふだけを書く。

規則:
- 合意するかどうかは「買い手の上限(max_price)>= 売り手の最低価格(floor)」だけで決まる。歩み寄り方には左右されない
- 上限が出品価格以上なら、1 往復目に出品価格で合意(出品価格より高くは買わせない)
- 買い手は「出品価格の 70%」(上限のほうが低ければ上限)から上限に向けて上げ、4 往復目に上限を出す
- 売り手は出品価格から最低価格に向けて下げるが、最低価格までは下げない(4 往復目でも、下げ幅全体の 60〜90% まで)
- 買い手の提示が売り手の提示以上になった往復で合意。合意額は 2 つの中間
- 4 往復目でも届かないとき:上限が最低価格以上なら、売り手が買い手の最後の提示(= 上限)を受け入れて合意。そうでなければ不成立

歩み寄る幅を毎回変える理由:幅が一定だと、1 往復分の提示を見るだけで相手の限度額を逆算できてしまう
(はじめの版がそうだった)。幅は seed から作る乱数で決める。seed が同じなら同じ幅になるので、テストでは結果を固定できる。
売り手の提示は切り上げ、最低価格そのものは出さない。
"""
import math
import random

ROUNDS = 4            # 往復の回数
START_PERCENT = 70    # 買い手が最初に出す金額(出品価格に対する %)
UNIT = 100            # 金額を丸める単位(円)
# 売り手が 4 往復目までに下げる割合(出品価格と最低価格の差に対して)。1.0 にしない = 最低価格は見せない
SELLER_LAST_STEP_MIN, SELLER_LAST_STEP_MAX = 0.60, 0.90
AGREED, NO_DEAL = "agreed", "no_deal"


def _round_to_unit(x: float) -> int:
    """100 円単位に四捨五入する(50 円ちょうどは切り上げ)。
    Python の round() は「偶数への丸め」で 4550 → 4500 と 4650 → 4700 が混ざるので使わない。"""
    return math.floor(x / UNIT + 0.5) * UNIT


def _clamp(x: int, low: int, high: int) -> int:
    return max(low, min(high, x))


def _steps(rng: random.Random) -> tuple[list[float], list[float]]:
    """各往復までに歩み寄る割合(0〜1、往復が進むほど大きい)を、買い手と売り手の分だけ作る。
    買い手は 4 往復目に 1.0(上限を出す)。売り手は 4 往復目でも 0.60〜0.90(最低価格までは下げない)。"""
    buyer = sorted(rng.random() for _ in range(ROUNDS - 1)) + [1.0]
    seller_last = rng.uniform(SELLER_LAST_STEP_MIN, SELLER_LAST_STEP_MAX)
    seller = [seller_last * x for x in sorted(rng.random() for _ in range(ROUNDS - 1))] + [seller_last]
    return buyer, seller


def negotiate(price: int, floor: int, max_price: int, seed) -> dict:
    """交渉を最後まで進める。price = 出品価格、floor = 売り手の最低価格、max_price = 買い手の上限、seed = 歩み寄る幅を決める乱数の種。
    返り値:{"outcome": "agreed" | "no_deal", "agreed_price": int | None,
            "rounds": [{"round": 1, "buyer_offer": int, "seller_ask": int}, ...]}
    合意したら、その往復で rounds は終わる。"""
    if max_price >= price:  # 定価で買える人には定価で売る。交渉で定価より高くなることはない
        return {"outcome": AGREED, "agreed_price": price,
                "rounds": [{"round": 1, "buyer_offer": price, "seller_ask": price}]}

    start = min(max_price, price * START_PERCENT // 100)
    buyer_steps, seller_steps = _steps(random.Random(seed))
    rounds = []
    for r in range(1, ROUNDS + 1):
        if r == ROUNDS:
            buyer_offer = max_price  # 最後は上限そのものを出す(買い手が実際に出す金額なので、見せてよい)
        else:
            # 買い手は切り捨て(上限より高く出さない)。出発点より下にもしない
            raw = start + (max_price - start) * buyer_steps[r - 1]
            buyer_offer = _clamp(math.floor(raw / UNIT) * UNIT, start, max_price)
        # 売り手は切り上げ(最低価格より下げない)。割合が 1.0 未満なので、最低価格 < 出品価格 なら必ず最低価格より高い
        raw = price - (price - floor) * seller_steps[r - 1]
        seller_ask = _clamp(math.ceil(raw / UNIT) * UNIT, floor, price)
        rounds.append({"round": r, "buyer_offer": buyer_offer, "seller_ask": seller_ask})
        if buyer_offer >= seller_ask:
            # 2つの提示の中間で合意する。100円単位には切り捨てる(売り手の提示を下回らない範囲で)。
            # なぜ切り捨てか:四捨五入だと、提示が ¥3,600 と ¥3,500 のとき中間 ¥3,550 が ¥3,600 に上がり、
            # 売り手が ¥3,500 でよいと言っているのに買い手が ¥3,600 払う、というおかしな結果になった(2026-11-15 に本番で確認)
            middle = max(seller_ask, math.floor((buyer_offer + seller_ask) / 2 / UNIT) * UNIT)
            return {"outcome": AGREED, "agreed_price": _clamp(middle, floor, min(max_price, price)), "rounds": rounds}

    if max_price >= floor:
        # 届かなかったが、買い手の最後の提示は最低価格以上 → 売り手がその金額を受け入れる。
        # こうしないと「上限 >= 最低価格なのに不成立」が起きる(売り手は最低価格まで下げて見せないため)
        rounds[-1]["seller_ask"] = max_price
        return {"outcome": AGREED, "agreed_price": max_price, "rounds": rounds}
    return {"outcome": NO_DEAL, "agreed_price": None, "rounds": rounds}


def rule_text() -> str:
    """合意の決め方を画面に出すための文。金額(とくに相手の限度額)は入れない。"""
    return ("合意するかどうかは、買い手の上限が売り手の最低価格以上かどうかだけで決まります。"
            f"2つのAIは最大{ROUNDS}往復で歩み寄り(歩み寄る幅は、相手の限度額を逆算できないように毎回変えます)、"
            "Gemini はせりふだけを書きます。")
