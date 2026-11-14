"""Gemini が遅い回の対策(3秒で返らなければもう1本出して、先に返った方を使う)のテスト。"""
import time
from types import SimpleNamespace

from app.services import ai


def _fake(monkeypatch, delays):
    calls = []

    def generate(prompt):
        i = len(calls)
        calls.append(i)
        time.sleep(delays[i])
        return SimpleNamespace(text='{"n": %d}' % i)

    monkeypatch.setattr(ai, "_gemini", lambda: object())
    monkeypatch.setattr(ai, "_generate", generate)
    monkeypatch.setattr(ai, "HEDGE_AFTER_SECONDS", 0.1)
    return calls


def test_fast_answer_is_not_duplicated(monkeypatch):
    calls = _fake(monkeypatch, [0.0])
    assert ai._ask_json("x") == {"n": 0}
    assert len(calls) == 1


def test_slow_answer_triggers_second_call_and_first_result_wins(monkeypatch):
    calls = _fake(monkeypatch, [1.0, 0.0])
    t0 = time.monotonic()
    assert ai._ask_json("x") == {"n": 1}
    assert len(calls) == 2
    assert time.monotonic() - t0 < 0.8
