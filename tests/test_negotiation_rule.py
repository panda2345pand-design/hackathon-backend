"""AI 同士の価格交渉の「数字の規則」の単体テスト(DB も Gemini も使わない)。"""
import pytest

from app.services.negotiation_rule import negotiate, rule_text


def test_contract_example():
    """docs/api.md の例:出品 6,500 円・最低 5,600 円・上限 6,000 円 → 4往復目に 5,800 円で合意。"""
    out = negotiate(price=6500, floor=5600, max_price=6000)
    assert out["outcome"] == "agreed" and out["agreed_price"] == 5800
    assert out["rounds"] == [
        {"round": 1, "buyer_offer": 4900, "seller_ask": 6300},
        {"round": 2, "buyer_offer": 5300, "seller_ask": 6100},
        {"round": 3, "buyer_offer": 5600, "seller_ask": 5800},
        {"round": 4, "buyer_offer": 6000, "seller_ask": 5600},
    ]


@pytest.mark.parametrize("max_price", [5000, 5001, 9_999_999])
def test_max_at_or_above_list_price_agrees_in_round_1_at_list_price(max_price):
    out = negotiate(price=5000, floor=4000, max_price=max_price)
    assert out == {"outcome": "agreed", "agreed_price": 5000,
                   "rounds": [{"round": 1, "buyer_offer": 5000, "seller_ask": 5000}]}


def test_one_yen_below_list_price_is_negotiated_and_never_exceeds_list_price():
    out = negotiate(price=5000, floor=4000, max_price=4999)
    assert out["outcome"] == "agreed" and out["agreed_price"] <= 4999


def test_agreement_in_the_middle():
    """上限に余裕があると、4往復を待たずに途中で合意する。"""
    out = negotiate(price=10000, floor=6000, max_price=9900)
    # 1往復目 7700 / 9000、2往復目 8500 / 8000 → ここで合意。中間 8250 → 8300(50 円ちょうどは切り上げ)
    assert out["rounds"] == [{"round": 1, "buyer_offer": 7700, "seller_ask": 9000},
                             {"round": 2, "buyer_offer": 8500, "seller_ask": 8000}]
    assert out["outcome"] == "agreed" and out["agreed_price"] == 8300


def test_no_deal_when_max_is_below_floor():
    out = negotiate(price=5000, floor=4500, max_price=4400)
    assert out["outcome"] == "no_deal" and out["agreed_price"] is None
    assert [r["round"] for r in out["rounds"]] == [1, 2, 3, 4]
    assert all(r["buyer_offer"] < r["seller_ask"] for r in out["rounds"])
    assert all(r["buyer_offer"] <= 4400 and r["seller_ask"] >= 4500 for r in out["rounds"])


def test_max_equal_to_floor_agrees_at_that_price():
    out = negotiate(price=5000, floor=4500, max_price=4500)
    assert out["outcome"] == "agreed" and out["agreed_price"] == 4500 and len(out["rounds"]) == 4


def test_amounts_are_rounded_to_100_yen():
    out = negotiate(price=6480, floor=5230, max_price=5990)
    for r in out["rounds"][:-1]:  # 最後の往復だけは限度額そのもの
        assert r["buyer_offer"] % 100 == 0 and r["seller_ask"] % 100 == 0
    assert out["outcome"] == "agreed" and out["agreed_price"] % 100 == 0


def test_last_round_uses_exact_limits_so_close_limits_still_agree():
    """上限 5,040 円・最低 5,020 円。100 円単位に丸めると届かなくなる組み合わせでも合意し、合意額は 2 つの間に収まる。"""
    out = negotiate(price=6000, floor=5020, max_price=5040)
    assert out["outcome"] == "agreed" and 5020 <= out["agreed_price"] <= 5040
    assert out["rounds"][-1] == {"round": 4, "buyer_offer": 5040, "seller_ask": 5020}


def test_floor_equal_to_price():
    """最低価格 = 出品価格(値下げしない)。定価未満の上限では不成立、定価以上なら定価で合意。"""
    out = negotiate(price=3000, floor=3000, max_price=2999)
    assert out["outcome"] == "no_deal" and all(r["seller_ask"] == 3000 for r in out["rounds"])
    assert negotiate(price=3000, floor=3000, max_price=3000)["agreed_price"] == 3000


def test_tiny_prices():
    assert negotiate(price=100, floor=100, max_price=100)["agreed_price"] == 100
    assert negotiate(price=100, floor=100, max_price=99)["outcome"] == "no_deal"
    out = negotiate(price=100, floor=50, max_price=60)
    assert out["outcome"] == "agreed" and 50 <= out["agreed_price"] <= 60
    out = negotiate(price=100, floor=1, max_price=1)
    assert out["outcome"] == "agreed" and out["agreed_price"] == 1
    assert all(r["buyer_offer"] >= 1 for r in out["rounds"])  # 0 円の提示は出さない


def test_low_max_starts_at_max():
    """上限が出品価格の 70% より低いとき、買い手は最初から上限を出す(上限より高くは出せない)。"""
    out = negotiate(price=10000, floor=5000, max_price=5500)
    assert [r["buyer_offer"] for r in out["rounds"]] == [5500, 5500, 5500, 5500]
    assert out["outcome"] == "agreed" and out["agreed_price"] == 5300  # 4往復目:5500 と 5000 の中間 5250 → 5300


def test_invariants_over_many_inputs():
    """たくさんの組み合わせで、必ず守られるはずの性質を確かめる。"""
    for price in (100, 300, 350, 990, 1000, 1234, 5000, 6480, 99_900, 1_234_567):
        floors = {1, price // 2, price * 8 // 10, price - 1, price} - {0}
        for floor in floors:
            for max_price in {1, floor - 1, floor, floor + 1, price * 7 // 10, price - 1, price, price + 1} - {0, -1}:
                out = negotiate(price, floor, max_price)
                where = f"price={price} floor={floor} max={max_price} -> {out}"
                assert (out["outcome"] == "agreed") == (max_price >= floor), where  # 成立するのは 上限 >= 最低価格 のときだけ
                assert 1 <= len(out["rounds"]) <= 4, where
                for a, b in zip(out["rounds"], out["rounds"][1:]):  # 買い手は下げない・売り手は上げない
                    assert a["buyer_offer"] <= b["buyer_offer"] and a["seller_ask"] >= b["seller_ask"], where
                for r in out["rounds"]:
                    assert 1 <= r["buyer_offer"] <= min(max_price, price) and floor <= r["seller_ask"] <= price, where
                if out["outcome"] == "agreed":
                    assert floor <= out["agreed_price"] <= min(max_price, price), where
                    last = out["rounds"][-1]
                    assert last["buyer_offer"] >= last["seller_ask"], where
                    assert all(r["buyer_offer"] < r["seller_ask"] for r in out["rounds"][:-1]), where  # 合意は「はじめて届いた往復」
                else:
                    assert out["agreed_price"] is None and len(out["rounds"]) == 4, where


def test_same_input_same_result():
    assert negotiate(6500, 5600, 6000) == negotiate(6500, 5600, 6000)


def test_rule_text_has_no_amounts():
    """決め方の説明に金額を入れない(相手の限度額が文から漏れないように)。"""
    for outcome in ("agreed", "no_deal"):
        text = rule_text(outcome)
        assert "往復" in text and "¥" not in text and "円単位" in text
    assert "不成立" in rule_text("no_deal") and "不成立" not in rule_text("agreed")
