"""Gemini のウォームアップ(app/services/ai.py の warm_up)のテスト。ネットワークには出ない。"""
import threading

from app.config import settings
from app.services import ai


def test_startup_works_without_gemini(client, monkeypatch):
    """Gemini 未設定でも起動でき(client フィクスチャが起動を通している)、ウォームアップは何もしない。"""
    assert client.get("/health").status_code == 200
    assert ai._configured() is False
    assert ai.warm_up() is None
    assert not [t for t in threading.enumerate() if t.name == "gemini-warm-up"]
    assert ai._client is None  # クライアントを作ろうともしていない


class _FakeModels:
    def __init__(self, error: Exception | None = None):
        self.calls, self.error = [], error

    def generate_content(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error


class _FakeClient:
    def __init__(self, error: Exception | None = None):
        self.models = _FakeModels(error)


def test_warm_up_calls_gemini_once_in_daemon_thread(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(settings, "gemini_api_key", "dummy")
    monkeypatch.setattr(ai, "_client", fake)  # 本物のクライアントは作らせない
    t = ai.warm_up()
    assert t is not None and t.daemon
    t.join(timeout=5)
    assert len(fake.models.calls) == 1
    call = fake.models.calls[0]
    assert call["model"] == settings.gemini_model and call["config"] is ai._GEN_CONFIG  # _ask_json と同じ設定


def test_warm_up_never_raises(monkeypatch):
    fake = _FakeClient(error=RuntimeError("boom"))
    monkeypatch.setattr(settings, "gemini_api_key", "dummy")
    monkeypatch.setattr(ai, "_client", fake)
    t = ai.warm_up()
    t.join(timeout=5)
    assert not t.is_alive() and len(fake.models.calls) == 1  # 失敗はログに出すだけ
