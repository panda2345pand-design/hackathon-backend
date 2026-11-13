"""交渉の通りやすさ(数字の規則)の単体テスト。"""
from app.services.ai import acceptance_level, check_rules


def test_acceptance_by_ratio():
    assert acceptance_level(9000, 10000, None)[0] == "high"
    assert acceptance_level(8000, 10000, None)[0] == "mid"
    assert acceptance_level(6000, 10000, None)[0] == "low"


def test_acceptance_uses_predicted_price():
    # 出品価格が AI の目安より高く、希望額が目安に近い → 一段通りやすい
    assert acceptance_level(8000, 10000, 8000)[0] == "high"
    # 目安の7割を下回る → 低い
    assert acceptance_level(7600, 10000, 12000)[0] == "low"


def test_rules():
    assert check_rules("ふつうのTシャツ") is None
    assert check_rules("Rolex replica watch") is not None
