"""AI 同士の価格交渉の「数字の規則」の単体テスト(DB も Gemini も使わない)。"""
import random

import pytest

from app.services.negotiation_rule import negotiate, rule_text

SEEDS = range(40)


@pytest.mark.parametrize("max_price", [5000, 5001, 9_999_999])
def test_max_at_or_above_list_price_agrees_in_round_1_at_list_price(max_price):
    for seed in SEEDS:
        out = negotiate(price=5000, floor=4000, max_price=max_price, seed=seed)
        assert out == {"outcome": "agreed", "agreed_price": 5000,
                       "rounds": [{"round": 1, "buyer_offer": 5000, "seller_ask": 5000}]}


def test_same_seed_same_result():
    assert negotiate(6500, 5600, 6000, seed="item:buyer:1") == negotiate(6500, 5600, 6000, seed="item:buyer:1")
    assert negotiate(6500, 5600, 6000, seed=7) == negotiate(6500, 5600, 6000, seed=7)


def test_fixed_example():
    """seed を決めれば結果は固定(乱数の列が変わっていないことの確認)。出品 10,000 円・最低 6,000 円・上限 9,900 円。"""
    out = negotiate(price=10000, floor=6000, max_price=9900, seed=1)
    assert out["outcome"] == "agreed" and 6000 <= out["agreed_price"] <= 9900
    last = out["rounds"][-1]
    assert last["buyer_offer"] >= last["seller_ask"]
    assert out["agreed_price"] == min(9900, max(6000, (last["buyer_offer"] + last["seller_ask"] + 100) // 200 * 100))  # 2 つの中間


def test_different_seeds_give_different_steps():
    """歩み寄る幅は seed ごとに違う(幅が一定だと、提示から相手の限度額を逆算できてしまう)。"""
    def asks(seed):
        return tuple(r["seller_ask"] for r in negotiate(100_000, 50_000, 40_000, seed)["rounds"])

    def offers(seed):
        return tuple(r["buyer_offer"] for r in negotiate(100_000, 99_000, 90_000, seed)["rounds"])

    assert len({asks(s) for s in SEEDS}) > 30 and len({offers(s) for s in SEEDS}) > 30
    # 同じ出品価格・同じ最低価格でも、最後の提示(4 往復目)がばらつく → 最後の提示から最低価格は決まらない
    assert len({asks(s)[-1] for s in SEEDS}) > 20
    for s in SEEDS:  # 1 回の交渉の中でも、下げ幅は一定ではない
        a = asks(s)
        assert len({a[0] - a[1], a[1] - a[2], a[2] - a[3]}) > 1 or a[0] == a[3], a


def test_no_deal_when_max_is_below_floor():
    for seed in SEEDS:
        out = negotiate(price=5000, floor=4500, max_price=4400, seed=seed)
        assert out["outcome"] == "no_deal" and out["agreed_price"] is None
        assert [r["round"] for r in out["rounds"]] == [1, 2, 3, 4]
        assert out["rounds"][-1]["buyer_offer"] == 4400  # 買い手は最後に上限を出す
        assert all(r["seller_ask"] > 4500 for r in out["rounds"])  # 売り手の提示に、最低価格そのものは出てこない
        assert all(r["buyer_offer"] < r["seller_ask"] for r in out["rounds"])


def test_seller_accepts_final_offer_when_asks_did_not_cross():
    """上限 = 最低価格。売り手は最低価格まで下げて見せないので提示は届かないが、買い手の最後の提示を受け入れて合意する。"""
    for seed in SEEDS:
        out = negotiate(price=5000, floor=4500, max_price=4500, seed=seed)
        assert out["outcome"] == "agreed" and out["agreed_price"] == 4500 and len(out["rounds"]) == 4
        assert out["rounds"][-1] == {"round": 4, "buyer_offer": 4500, "seller_ask": 4500}  # 最後の往復の売り手の提示 = 合意額
        assert all(r["seller_ask"] > 4500 for r in out["rounds"][:-1])


def test_amounts_are_rounded_to_100_yen():
    for seed in SEEDS:
        out = negotiate(price=6480, floor=5230, max_price=5990, seed=seed)
        for r in out["rounds"][:-1]:
            assert r["buyer_offer"] % 100 == 0 or r["buyer_offer"] == 6480 * 70 // 100, r  # 出発点(70%)だけは端数あり
            assert r["seller_ask"] % 100 == 0 or r["seller_ask"] == 6480, r                 # 出品価格そのものだけは端数あり
        assert out["outcome"] == "agreed"


def test_floor_equal_to_price():
    """最低価格 = 出品価格(値下げしない)。定価未満の上限では不成立、定価以上なら定価で合意。"""
    for seed in SEEDS:
        out = negotiate(price=3000, floor=3000, max_price=2999, seed=seed)
        assert out["outcome"] == "no_deal" and all(r["seller_ask"] == 3000 for r in out["rounds"])
        assert negotiate(price=3000, floor=3000, max_price=3000, seed=seed)["agreed_price"] == 3000


def test_tiny_prices():
    for seed in SEEDS:
        assert negotiate(100, 100, 100, seed)["agreed_price"] == 100
        assert negotiate(100, 100, 99, seed)["outcome"] == "no_deal"
        out = negotiate(100, 50, 60, seed)
        assert out["outcome"] == "agreed" and 50 <= out["agreed_price"] <= 60
        out = negotiate(100, 1, 1, seed)
        assert out["outcome"] == "agreed" and out["agreed_price"] == 1
        assert all(r["buyer_offer"] >= 1 for r in out["rounds"])  # 0 円の提示は出さない


def test_low_max_starts_at_max():
    """上限が出品価格の 70% より低いとき、買い手は最初から上限を出す(上限より高くは出せない)。"""
    for seed in SEEDS:
        out = negotiate(price=10000, floor=5000, max_price=5500, seed=seed)
        assert all(r["buyer_offer"] == 5500 for r in out["rounds"])
        assert out["outcome"] == "agreed" and 5000 <= out["agreed_price"] <= 5500


def _check_invariants(price, floor, max_price, seed):
    out = negotiate(price, floor, max_price, seed)
    where = f"price={price} floor={floor} max={max_price} seed={seed} -> {out}"
    rounds = out["rounds"]
    assert (out["outcome"] == "agreed") == (max_price >= floor), where  # 成立するのは 上限 >= 最低価格 のときだけ
    assert 1 <= len(rounds) <= 4 and [r["round"] for r in rounds] == list(range(1, len(rounds) + 1)), where
    for a, b in zip(rounds, rounds[1:]):  # 買い手は下げない・売り手は上げない
        assert a["buyer_offer"] <= b["buyer_offer"] and a["seller_ask"] >= b["seller_ask"], where
    for r in rounds:  # どんな結果でも、売り手は最低価格より下げず、買い手は上限(と出品価格)より高く出さない
        assert floor <= r["seller_ask"] <= price and 1 <= r["buyer_offer"] <= min(max_price, price), where
    if out["outcome"] == "agreed":
        assert floor <= out["agreed_price"] <= min(max_price, price), where
        assert rounds[-1]["buyer_offer"] >= rounds[-1]["seller_ask"], where
        assert all(r["buyer_offer"] < r["seller_ask"] for r in rounds[:-1]), where  # 合意は「はじめて届いた往復」
    else:
        assert out["agreed_price"] is None and len(rounds) == 4, where
        assert rounds[-1]["buyer_offer"] == max_price, where
        # 不成立のとき、売り手の提示に最低価格そのものは出ない(最低価格 = 出品価格のときは、出品価格として公開済み)
        assert all(r["seller_ask"] > floor or floor == price for r in rounds), where
    assert out == negotiate(price, floor, max_price, seed), where  # 同じ seed なら同じ結果


def test_invariants_on_edge_values():
    for price in (100, 300, 350, 990, 1000, 1234, 5000, 6480, 99_900, 1_234_567):
        for floor in {1, price // 2, price * 8 // 10, price - 1, price} - {0}:
            for max_price in {1, floor - 1, floor, floor + 1, price * 7 // 10, price - 1, price, price + 1} - {0, -1}:
                for seed in range(8):
                    _check_invariants(price, floor, max_price, seed)


def test_invariants_on_random_values():
    """乱数で作った 5,000 通りの 出品価格・最低価格・上限・seed で、必ず守られるはずの性質を確かめる。"""
    rnd = random.Random(2026)
    for _ in range(5000):
        price = rnd.choice([rnd.randint(1, 500), rnd.randint(300, 20_000), rnd.randint(300, 9_999_999)])
        floor = rnd.randint(1, price)
        max_price = rnd.choice([rnd.randint(1, price + 10), rnd.randint(max(1, floor - 300), floor + 300)])
        _check_invariants(price, floor, max_price, rnd.randint(0, 10**9))


def test_rule_text():
    """決め方の説明:金額を入れない。成立の条件・幅を毎回変えること・Gemini はせりふだけ、を言う。"""
    text = rule_text()
    assert "¥" not in text and not any(ch.isdigit() for ch in text.replace("2つ", "").replace("最大4往復", ""))
    assert "上限" in text and "最低価格" in text and "逆算" in text and "Gemini" in text
    assert text.count("。") == 2
