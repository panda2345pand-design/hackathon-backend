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


def _ml_title(title: str) -> tuple[str, bool]:
    from app.services import ai  # 循環 import を避けるため関数の中で読む
    return ai.title_for_ml(title)


def predict_price(title: str, category: str, brand: str | None, condition: str) -> dict:
    p = _predictor()
    if not p:
        return dict(_UNAVAILABLE_PRICE)
    try:
        en, translated = _ml_title(title)
        out = p.predict_price(en, category, brand, condition)
        if translated and out.get("available"):  # 透明化:英語に直して推定したことを理由に出す
            out["basis"] = [*out.get("basis", []), {"feature": "商品名の英訳", "value": en,
                                                    "effect": "モデルは英語の単語で学習しているため、Gemini で英語に直してから推定しました"}]
        return out
    except Exception as e:
        log.warning("predict_price failed: %r", e)
        return dict(_UNAVAILABLE_PRICE)


def assign_cluster(title: str, category: str) -> dict:
    p = _predictor()
    if p:
        try:
            return p.assign_cluster(_ml_title(title)[0], category)
        except Exception as e:
            log.warning("assign_cluster failed: %r", e)
    return {"available": False, "cluster_id": None, "cluster_label": None}


def cluster_labels() -> dict[int, str]:
    p = _predictor()
    try:
        return {int(k): v for k, v in p.cluster_labels().items()} if p else {}
    except Exception:
        return {}


def cluster_labels_ja() -> dict[int, str]:
    """クラスタの日本語名。無いものは英語のラベル。モデルが無ければ空。"""
    p = _predictor()
    try:
        return {int(k): v for k, v in p.cluster_labels_ja().items()} if p else {}
    except Exception:
        return {}


def hidden_clusters() -> set[int]:
    """ホームのチップに出さないクラスタ(中身が寄せ集めのもの)。"""
    p = _predictor()
    try:
        return {int(k) for k in p.hidden_clusters()} if p else set()
    except Exception:
        return set()


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


# モデルが無いとき用の既定の選択肢(モデルがあれば、モデルが知っているカテゴリ・状態を使う)
DEFAULT_CATEGORIES = ["Women", "Men", "Kids", "Home", "Electronics", "Beauty", "Toys & Collectibles", "Sports & Outdoors", "Other"]
DEFAULT_CONDITIONS = ["New", "Like new", "Good", "Fair", "Poor"]


def category_choices() -> list[str]:
    """画面の選択肢に出すカテゴリ(GET /categories と、写真からの下書きで同じ一覧を使う)。"""
    return categories() or DEFAULT_CATEGORIES


def condition_choices() -> list[str]:
    return conditions() or DEFAULT_CONDITIONS


# 2本目の教師あり学習(いいねされやすさ)は、検証して載せないと決めた。画面が「無い」と分かるように理由つきで返す
ATTENTION_UNAVAILABLE = {"available": False,
                         "reason": "精度が事前の基準(AUC 0.65)に届かなかったため載せていない(test AUC 0.612)。詳しくは ml/LIKE_REPORT.md"}


def model_info() -> dict:
    p = _predictor()
    try:
        return p.model_info() if p else {}
    except Exception:
        return {}
