"""学習済みモデル(app/ml/predictor.py)への入口。モデルが無い・壊れていてもアプリ本体は止めない。"""
import logging

log = logging.getLogger(__name__)

_UNAVAILABLE_PRICE = {"available": False, "predicted_price": None, "low": None, "high": None, "currency": "JPY",
                      "basis": [], "model": None}


def _predictor():
    try:
        from app.ml import predictor
        return predictor
    except Exception as e:  # モデルのファイルやライブラリが無い環境でも API は動かす
        log.warning("ML predictor unavailable: %r", e)
        return None


def predict_price(title: str, category: str, brand: str | None, condition: str) -> dict:
    p = _predictor()
    if not p:
        return dict(_UNAVAILABLE_PRICE)
    try:
        return p.predict_price(title, category, brand, condition)
    except Exception as e:
        log.warning("predict_price failed: %r", e)
        return dict(_UNAVAILABLE_PRICE)


def assign_cluster(title: str, category: str) -> dict:
    p = _predictor()
    if p:
        try:
            return p.assign_cluster(title, category)
        except Exception as e:
            log.warning("assign_cluster failed: %r", e)
    return {"available": False, "cluster_id": None, "cluster_label": None}


def cluster_labels() -> dict[int, str]:
    p = _predictor()
    try:
        return {int(k): v for k, v in p.cluster_labels().items()} if p else {}
    except Exception:
        return {}


def categories() -> list[str]:
    p = _predictor()
    try:
        return list(p.categories()) if p else []
    except Exception:
        return []


def conditions() -> list[str]:
    p = _predictor()
    try:
        return list(p.conditions()) if p else []
    except Exception:
        return []


def model_info() -> dict:
    p = _predictor()
    try:
        return p.model_info() if p else {}
    except Exception:
        return {}
