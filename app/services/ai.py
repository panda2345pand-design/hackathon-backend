"""Gemini を使う処理(ユースケース層)。説明文の生成・出品禁止物のチェック・価格交渉の手伝い。

設計の考え方:
- Gemini が使えない(キーが無い・失敗・時間切れ)ときも機能は止めず、決め打ちの応答を返して source="fallback" と明示する。
- 「判定」は説明できる形にする:禁止物はまずキーワードの規則で見て、次に Gemini。交渉の通りやすさは数字の規則で決め、Gemini は文章だけを書く。
"""
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed, wait
import logging
import threading
import time
import unicodedata

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


# _ask_json とウォームアップで同じ設定を使う(同じ経路を温めるため、1か所にまとめる)
# thinking_budget=0:考える時間を切る。実測で 1回 8秒 → 2秒(2026-11-14)。短い文章と判定には十分
# system_instruction の1文は速さのため。thinking_budget=0 だけでは、約半分の回でモデルが考えてしまい 5〜13 秒かかった。
# この1文を足すと 10回中10回 考えずに 1.3〜3.4 秒で返った(2026-11-15 実測)
_GEN_CONFIG = {"response_mime_type": "application/json", "temperature": 0.4, "thinking_config": {"thinking_budget": 0},
               "system_instruction": "考える過程は不要です。すぐに答えだけを出力してください。"}


def _configured() -> bool:
    """Gemini の接続先が設定されているか(クライアントは作らずに、設定だけを見る)。"""
    return bool(settings.gemini_api_key or (settings.gemini_use_vertex and settings.gemini_project))


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


def _generate(prompt):
    """Gemini を1回呼ぶ。かかった時間と「考えるのに使ったトークン数」をログに出す。
    prompt は文字列。写真を見せるときだけ [写真, 文字列] のリスト(describe_image)。"""
    t0 = time.monotonic()
    r = _gemini().models.generate_content(model=settings.gemini_model, contents=prompt, config=_GEN_CONFIG)
    usage = getattr(r, "usage_metadata", None)
    log.info("Gemini call %.1fs (thoughts=%s)", time.monotonic() - t0, getattr(usage, "thoughts_token_count", None))
    return r


# 同じ問い合わせをもう1本出すまでの待ち時間(秒)と、あきらめるまでの時間(秒)
HEDGE_AFTER_SECONDS, GIVE_UP_SECONDS = 3.0, 25.0
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")


def _ask_json(prompt) -> dict | None:
    """Gemini に JSON で答えさせる。失敗したら None(呼び出し側が fallback に切り替える)。

    遅い回への対策(2026-11-15 実測):同じ問い合わせでも、約2秒で返る回と 5〜13秒かかる回がある。
    原因は、thinking_budget=0 を指定してもモデルが考えてしまう回があること(ログの thoughts が 400〜1800 トークンの回が遅い)。
    モデル側の動きでこちらからは止められないので、3秒たっても返らなければ同じ問い合わせをもう1本出し、先に返った方を使う。
    代わりに、遅い回は Gemini を2回呼ぶ分だけ料金が増える。
    """
    if not _gemini():
        return None
    futures = [_pool.submit(_generate, prompt)]
    done, _ = wait(futures, timeout=HEDGE_AFTER_SECONDS)
    if not done:
        futures.append(_pool.submit(_generate, prompt))
    try:
        for f in as_completed(futures, timeout=GIVE_UP_SECONDS):
            try:
                data = json.loads(f.result().text)
                if isinstance(data, dict):
                    return data
            except Exception as e:  # 片方が失敗しても、もう片方を待つ
                log.warning("Gemini call failed: %r", e)
    except TimeoutError:
        log.warning("Gemini call timed out after %.0fs", GIVE_UP_SECONDS)
    return None


# ---------- ウォームアップ(起動直後の1回目だけ遅い問題への対策) ----------
def _warm_up_job() -> None:
    """クライアントを作り、ごく短い問い合わせを1回だけ投げる。何が起きても例外は外に出さない。"""
    t0 = time.monotonic()
    try:
        client = _gemini()
        if not client:
            return
        client.models.generate_content(model=settings.gemini_model, contents='出力は JSON: {"ok": true}', config=_GEN_CONFIG)
        log.info("Gemini warm-up done in %.1fs", time.monotonic() - t0)
    except Exception as e:  # 失敗しても本番の呼び出しは普通に動く(1回目が遅いままになるだけ)
        log.warning("Gemini warm-up failed after %.1fs: %r", time.monotonic() - t0, e)


def warm_up() -> threading.Thread | None:
    """起動時に呼ぶ。別スレッドで Gemini を温める(起動は待たせない)。

    なぜ:Cloud Run の新しいインスタンスでは、1回目の /ai/describe だけサーバー側で 10〜12 秒かかった(2回目以降は 2〜5 秒。実測)。
    クライアントの作成・Vertex の認証・接続の確立が1回目に乗るため。それを利用者の最初の操作より前に済ませておく。
    - Gemini が未設定なら何もしない(テストとローカルはオフラインのまま・速いまま)。
    - daemon スレッドなので、終了時にこのスレッドを待たない。
    """
    if not _configured():
        return None
    try:
        t = threading.Thread(target=_warm_up_job, name="gemini-warm-up", daemon=True)
        t.start()
        return t
    except Exception as e:
        log.warning("Gemini warm-up thread could not start: %r", e)
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


# ---------- 4. AI 同士の価格交渉のせりふ ----------
MESSAGE_CHARS = 60        # せりふ 1 つの長さ(Gemini に頼む長さ。定型文もこの中に収める)
MESSAGE_CHARS_LIMIT = 80  # これを超えたら受け取らない。Gemini は字数を正確には数えないので、頼んだ長さより少し長いものまでは通す


def _mentions(text: str, amount: int) -> bool:
    """文の中にその金額が入っているか(5,800 と 5800 のどちらの書き方でもよい。全角の数字は半角に直してから見る)。"""
    plain = unicodedata.normalize("NFKC", text)
    return f"{amount:,}" in plain or str(amount) in plain


def _valid_messages(data: dict | None, rounds: list[dict]) -> list[dict] | None:
    """Gemini の返事が頼んだ形か。往復の数が合い、どのせりふも短く、その往復の金額が入っていれば、せりふの一覧を返す。
    1 つでも合わなければ None(全体を定型文にする。金額は規則で決めたものなので、違う金額のせりふは出さない)。"""
    got = (data or {}).get("rounds")
    if not isinstance(got, list) or len(got) != len(rounds):
        return None
    out = []
    for r, m in zip(rounds, got):
        if not isinstance(m, dict):
            return None
        buyer, seller = m.get("buyer_message"), m.get("seller_message")
        if not (isinstance(buyer, str) and isinstance(seller, str)):
            return None
        buyer, seller = buyer.strip(), seller.strip()
        if not (0 < len(buyer) <= MESSAGE_CHARS_LIMIT and 0 < len(seller) <= MESSAGE_CHARS_LIMIT):
            return None
        if not (_mentions(buyer, r["buyer_offer"]) and _mentions(seller, r["seller_ask"])):
            return None
        out.append({"buyer_message": buyer, "seller_message": seller})
    return out


def _fallback_messages(rounds: list[dict], agreed_price: int | None) -> list[dict]:
    """Gemini が使えないときの定型のせりふ。同じ入力なら必ず同じ文になる。
    「ここまでなら出せる」「これより下げられない」のような、限度額だと分かる言い方はしない。"""
    out = []
    for r in rounds:
        first, last = r["round"] == 1, r is rounds[-1]
        buyer_offer, seller_ask = f"¥{r['buyer_offer']:,}", f"¥{r['seller_ask']:,}"
        if first:
            buyer = f"はじめまして。{buyer_offer}でお譲りいただけないでしょうか。"
        elif last:
            buyer = f"{buyer_offer}でいかがでしょうか。ご検討をお願いします。"
        else:
            buyer = f"では、{buyer_offer}ではいかがでしょうか。"
        if last and agreed_price is not None:
            if r["seller_ask"] == agreed_price:
                seller = f"ありがとうございます。{seller_ask}でお譲りします。"
            else:
                seller = f"{seller_ask}と考えていましたが、間を取って¥{agreed_price:,}で成立とさせてください。"
        elif last:
            seller = f"{seller_ask}でいかがでしょうか。今回はこれ以上は難しいです。"
        elif first:
            seller = f"ご提案ありがとうございます。{seller_ask}でいかがでしょうか。"
        else:
            seller = f"もう少し歩み寄って、{seller_ask}ではいかがでしょうか。"
        out.append({"buyer_message": buyer, "seller_message": seller})
    return out


def negotiation_messages(title: str, rounds: list[dict], outcome: str, agreed_price: int | None) -> tuple[list[dict], str]:
    """AI 同士の価格交渉の、各往復のせりふを書く。返り値は ([{"buyer_message", "seller_message"}, ...], "gemini" | "fallback")。

    - 金額と結果は規則(negotiation_rule)で決まっている。Gemini はそれを言葉にするだけ。
    - Gemini を呼ぶのは、会話全体で 1 回(往復ごとに呼ぶと 4 倍の時間と料金がかかり、前後のせりふもつながらない)。
    - Gemini に渡すのは、商品名と各往復の金額だけ。売り手の最低価格・買い手の上限を「限度額」としては渡さない
      (渡していない言葉は、せりふに出てこない)。
    """
    lines = [f"{r['round']}往復目: 買い手の提示 {r['buyer_offer']}円 / 売り手の提示 {r['seller_ask']}円" for r in rounds]
    ending = f"{len(rounds)}往復目に {agreed_price}円で合意" if agreed_price is not None else "合意できずに終了"
    data = _ask_json(
        "あなたは、フリマアプリで買い手のAIと売り手のAIが価格交渉をする会話の、せりふを書く係です。"
        "金額と結果はすでに決まっていて、変えてはいけません。\n"
        f"商品: {title}\n" + "\n".join(lines) + f"\n結果: {ending}\n"
        "各往復について、buyer_message(買い手のAIのせりふ)と seller_message(売り手のAIのせりふ)を日本語で書いてください。\n"
        f"条件:丁寧で短く、それぞれ{MESSAGE_CHARS}字以内。buyer_message にはその往復の買い手の提示額を、"
        "seller_message にはその往復の売り手の提示額を必ず入れる。最後の往復のせりふは結果に合わせる。"
        "「これが精一杯です」「これ以上は下げられません」のように、その金額が限界だと分かる言い方はしない。"
        "上に書かれていない金額や、商品について与えられていないこと(状態・付属品・理由など)は書かない。\n"
        '出力は JSON: {"rounds": [{"round": 1, "buyer_message": "...", "seller_message": "..."}]}')
    messages = _valid_messages(data, rounds)
    if messages:
        return messages, "gemini"
    return _fallback_messages(rounds, agreed_price), "fallback"


# ---------- 5. 写真から出品の下書きを作る ----------
_NO_DRAFT = {"title": None, "category": None, "condition": None, "brand": None, "description": None}


def _text_or_none(value, max_chars: int) -> str | None:
    """Gemini の返事の 1 項目を、空でない文字列(出品の入力欄に入る長さ)か None にする。"""
    return value.strip()[:max_chars] if isinstance(value, str) and value.strip() else None


def describe_image(data: bytes, content_type: str, categories: list[str], conditions: list[str]) -> dict:
    """保存してある写真を Gemini に見せて、出品の下書き(商品名・カテゴリ・状態・ブランド・説明文)を作る。

    - カテゴリと状態は、アプリの選択肢(categories / conditions)の中の値だけを通す。それ以外の答えは None にする
      (選択肢に無い値をそのまま画面に入れると、出品の入力欄と合わなくなるため)。
    - Gemini が使えない・答えが形になっていないときは、全部 None で source="fallback"(写真だけでは定型文を作れない)。
    """
    if not _gemini():
        return {**_NO_DRAFT, "source": "fallback"}
    from google.genai import types  # Gemini を使うときだけ読み込む(未設定の環境では要らない)
    answer = _ask_json([
        types.Part.from_bytes(data=data, mime_type=content_type),
        "あなたはフリマアプリの出品を手伝うアシスタントです。この写真の商品について、出品の下書きを作ってください。\n"
        "写真から分かることだけを書く。写真から分からないこと(サイズ・購入時期・付属品・動作確認など)は書かない。\n"
        "title = 商品名(40字以内の日本語。ブランド名や型番が読み取れれば入れる)。\n"
        f"category = 次のどれか 1 つ: {json.dumps(categories, ensure_ascii=False)}。自信がなければ null。\n"
        f"condition = 次のどれか 1 つ(左ほど状態が良い): {json.dumps(conditions, ensure_ascii=False)}。写真から判断できなければ null。\n"
        "brand = ロゴなどから読み取れたブランド名。分からなければ null。\n"
        "description = 説明文(80〜160字の日本語。誇大な表現は使わない)。\n"
        '出力は JSON: {"title": "...", "category": "...", "condition": "...", "brand": "...", "description": "..."}'])
    if not answer:
        return {**_NO_DRAFT, "source": "fallback"}
    title, description = _text_or_none(answer.get("title"), 100), _text_or_none(answer.get("description"), 3000)
    if not title and not description:  # 下書きとして使えるものが何も無い
        return {**_NO_DRAFT, "source": "fallback"}
    category, condition = answer.get("category"), answer.get("condition")
    return {"title": title,
            "category": category if category in categories else None,
            "condition": condition if condition in conditions else None,
            "brand": _text_or_none(answer.get("brand"), 80), "description": description, "source": "gemini"}
