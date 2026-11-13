"""Gemini を使う処理(ユースケース層)。説明文の生成・出品禁止物のチェック・価格交渉の手伝い。

設計の考え方:
- Gemini が使えない(キーが無い・失敗・時間切れ)ときも機能は止めず、決め打ちの応答を返して source="fallback" と明示する。
- 「判定」は説明できる形にする:禁止物はまずキーワードの規則で見て、次に Gemini。交渉の通りやすさは数字の規則で決め、Gemini は文章だけを書く。
"""
import json
import logging

from app.config import settings

log = logging.getLogger(__name__)
_client = None

# 規則で止めるもの(メルカリの「禁止されている出品物」を参考にした代表例。全部ではない)
PROHIBITED_RULES: list[tuple[str, list[str]]] = [
    ("武器・危険物は出品できません", ["拳銃", "実銃", "銃弾", "火薬", "爆発物", "スタンガン", "handgun", "firearm", "ammunition", "explosive"]),
    ("医薬品・処方薬は出品できません", ["処方薬", "処方箋", "医薬品", "睡眠薬", "prescription drug"]),
    ("違法薬物は出品できません", ["大麻", "覚醒剤", "コカイン", "マリファナ", "cannabis", "cocaine", "marijuana"]),
    ("偽ブランド品・コピー商品は出品できません", ["偽物", "コピー品", "レプリカ", "スーパーコピー", "n級", "fake", "replica", "counterfeit"]),
    ("たばこ・電子たばこのリキッド(ニコチン入り)は出品できません", ["たばこ", "タバコ", "煙草", "ニコチン", "cigarette", "nicotine"]),
    ("現金・金券類・個人情報は出品できません", ["現金", "紙幣", "クレジットカード", "口座", "個人情報", "名簿"]),
    ("生き物は出品できません", ["生体", "子犬", "子猫", "live animal"]),
]


def _gemini():
    """Gemini のクライアント(使えなければ None)。API キー方式と Vertex AI 方式の両方に対応。"""
    global _client
    if _client is not None:
        return _client
    try:
        from google import genai
        if settings.gemini_api_key:
            _client = genai.Client(api_key=settings.gemini_api_key)
        elif settings.gemini_use_vertex and settings.gemini_project:
            _client = genai.Client(vertexai=True, project=settings.gemini_project, location=settings.gemini_location)
    except Exception as e:
        log.warning("Gemini client unavailable: %r", e)
    return _client


def _ask_json(prompt: str) -> dict | None:
    """Gemini に JSON で答えさせる。失敗したら None(呼び出し側が fallback に切り替える)。"""
    client = _gemini()
    if not client:
        return None
    try:
        r = client.models.generate_content(model=settings.gemini_model, contents=prompt,
                                           # thinking_budget=0:考える時間を切る。実測で 1回 8秒 → 2秒(2026-11-14)。短い文章と判定には十分
                                           config={"response_mime_type": "application/json", "temperature": 0.4,
                                                   "thinking_config": {"thinking_budget": 0}})
        data = json.loads(r.text)
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("Gemini call failed: %r", e)
        return None


# ---------- 0. 商品名を英語にする(価格予測モデルは英語の単語しか知らないため) ----------
_en_cache: dict[str, str] = {}


def title_for_ml(title: str) -> tuple[str, bool]:
    """日本語などが入った商品名を、価格予測・クラスタ用に英語へ直す。返り値は (モデルに渡す商品名, 翻訳したか)。
    英数字だけならそのまま。Gemini が使えなければ元のまま(その場合、モデルはカテゴリ・ブランド・状態だけで決める)。"""
    if title.isascii():
        return title, False
    if title in _en_cache:
        return _en_cache[title], True
    data = _ask_json("次のフリマ商品の名前を、米国のフリマアプリの出品タイトルのような短い英語に直してください。"
                     "ブランド名・型番はそのまま。情報を足さない。\n"
                     f"商品名: {title}\n" '出力は JSON: {"title_en": "..."}')
    en = (data or {}).get("title_en")
    if isinstance(en, str) and en.strip():
        _en_cache[title] = en.strip()[:100]
        return _en_cache[title], True
    return title, False


# ---------- 1. 説明文の生成 ----------
def describe(title: str, category: str, condition: str, brand: str | None, keywords: str | None) -> dict:
    data = _ask_json(
        "あなたはフリマアプリの出品を手伝うアシスタントです。次の商品の説明文を日本語で書いてください。\n"
        "条件:120〜200字。事実として与えられた情報だけを使い、付属品・サイズ・購入時期など与えられていないことは書かない。"
        "誇大な表現(最高・絶対など)は使わない。最後に購入者への一言を1文。\n"
        f"商品名: {title}\nカテゴリ: {category}\n状態: {condition}\nブランド: {brand or '不明'}\n出品者のメモ: {keywords or 'なし'}\n"
        '出力は JSON: {"description": "..."}')
    if data and isinstance(data.get("description"), str) and data["description"].strip():
        return {"description": data["description"].strip(), "source": "gemini"}
    parts = [f"{brand}の" if brand else "", f"{title}です。", f"カテゴリは{category}、状態は「{condition}」です。"]
    if keywords:
        parts.append(f"{keywords}。")
    parts.append("気になる点があれば購入前にご質問ください。")
    return {"description": "".join(parts), "source": "fallback"}


# ---------- 2. 出品禁止物のチェック ----------
def check_rules(text: str) -> str | None:
    low = text.lower()
    for reason, words in PROHIBITED_RULES:
        for w in words:
            if w.lower() in low:
                return f"{reason}(「{w}」が含まれています)"
    return None


def check(title: str, description: str, category: str) -> dict:
    reason = check_rules(f"{title}\n{description}\n{category}")
    if reason:
        return {"ok": False, "reason": reason, "source": "rule"}
    data = _ask_json(
        "あなたはフリマアプリの出品審査担当です。次の出品が禁止出品物(武器・危険物、医薬品、違法薬物、偽ブランド品、"
        "たばこ・酒類、現金・金券、個人情報、生き物、アダルト、盗品が疑われるもの、法律に違反するもの)に当たるかを判定してください。"
        "ふつうの日用品・衣類・本・家電・おもちゃは問題ありません。迷う場合は ok=true とし、理由に注意点を書いてください。\n"
        f"商品名: {title}\n説明: {description}\nカテゴリ: {category}\n"
        '出力は JSON: {"ok": true または false, "reason": "日本語で1文"}')
    if data and isinstance(data.get("ok"), bool):
        return {"ok": data["ok"], "reason": str(data.get("reason") or ""), "source": "gemini"}
    return {"ok": True, "reason": "キーワードの規則では問題は見つかりませんでした(AI の確認は未接続)", "source": "fallback"}


# ---------- 3. 価格交渉の手伝い ----------
def acceptance_level(amount: int, price: int, predicted_price: int | None) -> tuple[str, str]:
    """通りやすさを数字の規則で決める(説明できるように Gemini には任せない)。返り値は (high|mid|low, 根拠の文)。"""
    ratio = amount / price
    level = "high" if ratio >= 0.9 else "mid" if ratio >= 0.75 else "low"
    why = f"希望額は出品価格の{round(ratio * 100)}%です"
    if predicted_price:
        if price > predicted_price * 1.15 and amount >= predicted_price * 0.9 and level != "high":
            level = "high" if level == "mid" else "mid"  # 出品価格が相場より高めで、希望額が相場に近い → 一段通りやすい
            why += f"。出品価格はAIの目安(¥{predicted_price:,})より高めで、希望額は目安に近いので通りやすめです"
        elif amount < predicted_price * 0.7:
            level = "low"
            why += f"。AIの目安(¥{predicted_price:,})の7割を下回っています"
        else:
            why += f"。AIの目安は¥{predicted_price:,}です"
    return level, why


def negotiate(title: str, price: int, predicted_price: int | None, amount: int) -> dict:
    level, why = acceptance_level(amount, price, predicted_price)
    data = _ask_json(
        "あなたはフリマアプリで買い手の価格交渉を手伝うアシスタントです。\n"
        f"商品: {title}\n出品価格: {price}円\nAIの適正価格の目安: {predicted_price or '不明'}円\n買い手の希望額: {amount}円\n"
        f"通りやすさの判定(規則で決定済み): {level}({why})\n"
        "次の2つを日本語で書いてください。reply = 買い手が出品者に送る、丁寧で短い値下げのお願い(80字以内。希望額を入れる。"
        "嘘の理由は作らない)。seller_tip = 出品者がこの提案を受けるか判断するための客観的な一言(60字以内)。\n"
        '出力は JSON: {"reply": "...", "seller_tip": "..."}')
    if data and isinstance(data.get("reply"), str) and isinstance(data.get("seller_tip"), str):
        reply, tip, source = data["reply"].strip(), data["seller_tip"].strip(), "gemini"
    else:
        reply = f"はじめまして。こちらの商品を購入したいと考えています。もし可能でしたら、¥{amount:,}にお値下げいただけないでしょうか。ご検討よろしくお願いします。"
        tip = why + "。"
        source = "fallback"
    return {"acceptance": level, "reply": reply, "seller_tip": tip, "predicted_price": predicted_price, "reason": why, "source": source}
