# -*- coding: utf-8 -*-
"""Bot-inbox harvester — forward TEXT or PHOTOS to your bot, both get used.

The user sends the delivery bot free text (tips, forwarded posts) and/or IMAGES
(bet slips, stat screenshots, team forms). This script:
  1. reads the bot's pending updates (getUpdates — TG_BOT_TOKEN only),
  2. DOWNLOADS every photo via its file_id and SAVES it to data/tg_media/,
  3. asks Gemini (multimodal: TEXT + VISION together) to extract ONLY the
     football tips explicitly present in the text and the images,
  4. writes them to output/tips_tg_inbox.json (merge_rank auto-loads it, so they
     ride into the prediction alongside every other source) plus a human-readable
     output/tg_inbox_manifest.json of what was received.

Layers, all fail-soft (no key / API error / no updates → empty output, pipeline
unaffected):
  - Gemini multimodal is the primary reader (honours "read the text AND the
    images"). One call per message covers its caption and its photo(s).
  - A regex pass (tg_tip_extract) runs alongside as a free, conservative net.
  - The older pluggable-vision path stays as a fallback for images when only a
    non-Gemini AI_API_KEY is configured.

Credentials: TG_BOT_TOKEN (required) + a Gemini key for vision
(GEMINI_API_KEY | GOOGLE_API_KEY | YT_API_KEY, or AI_API_KEY when
AI_PROVIDER=gemini). Photos are saved under data/tg_media/, which is gitignored
— bet-slip screenshots must never be committed to a public repo.
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from tg_tip_extract import extract_tips

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
MEDIA = os.path.join(HERE, "..", "data", "tg_media")
MAX_PHOTOS = 15  # per run, keeps AI usage (and download time) tiny

# One multimodal prompt covering both the message text and any attached images.
EXTRACT_PROMPT = """این پیام را یک کاربر برای دستیار پیش‌بینی فوتبال فرستاده و ممکن است شامل متن و/یا تصویر باشد (کوپن/اسلیپ شرط، آمار، فرم تیم‌ها، اسکرین‌شات — احتمالاً فارسی).
فقط و فقط تیپ‌های فوتبالی را که در متن یا تصویر **به‌صراحت** آمده استخراج کن — چیزی از خودت نساز.

خروجی را **دقیقاً** یک آرایهٔ JSON بده (بدون متن اضافه)، هر عضو:
{"home":"نام لاتین تیم میزبان","away":"نام لاتین تیم مهمان","league":"نام لیگ به انگلیسی در صورت ذکر","market":"کد بازار","pick":"انتخاب","line":"خط در صورت وجود مثل 2.5 یا -1 یا null","prob":عدد۰تا۱یاnull,"odds":عددیاnull,"note":"خلاصهٔ یک‌جمله‌ای فارسی از دلیل"}

market می‌تواند هر بازار شرط‌بندی معتبر باشد. از این کدها استفاده کن (و اگر بازار دیگری بود، نزدیک‌ترین کد یا یک کد کوتاهِ لاتینِ گویا بساز):
 - نتیجه:      1X2 (پیک: 1/X/2)   |   DC دبل‌شانس (1X/X2/12)   |   DNB بدون‌تساوی (1/2)
 - گل کل/تیم:  OU زیر/بالای گل با خط (پیک: Over/Under، مثال market=OU line=2.5) | OU_HOME | OU_AWAY
 - هر دو گل:   BTTS (Yes/No)
 - نتیجه دقیق: CS (پیک مثل "2-1")   |   HTFT نیمه/پایان (پیک مثل "1/1","X/2")
 - هندیکاپ:    AH هندیکاپ آسیایی (پیک: Home/Away با line مثل -1، -0.5، +1) | EH هندیکاپ اروپایی
 - کرنر:       CORN_OU کرنر زیر/بالا (Over/Under + line) | CORN_1X2 | CORN_TEAM
 - کارت:       CARD_OU کارت زیر/بالا (Over/Under + line) | CARD_TEAM
 - آفساید/شوت: OFF_OU | SHOT_OU (Over/Under + line)
 - نیمه اول:   HT_1X2 | HT_OU | HT_BTTS  (همان قواعد ولی مربوط به نیمهٔ اول)
 - بازیکن:     PLAYER_GOAL گلزن (pick=نام بازیکن) | PLAYER_ASSIST | PLAYER_CARD | PLAYER_SHOTS (+line)
 - ترکیبی:     COMBO برای شرط‌های ترکیبی/بت‌بیلدر (pick=توضیح کوتاه لاتین از کل ترکیب)

قواعد کلی:
 - نام تیم‌ها را حتماً لاتین و کامل بنویس (پرسپولیس→Persepolis، رئال→Real Madrid).
 - «برد میزبان»=1X2/1؛ «بالای ۲.۵»=OU/Over/line=2.5؛ «هر دو تیم گل»=BTTS/Yes؛ «شانس دوبل»=DC؛ «گلزن اول/هرزمان»=PLAYER_GOAL.
 - خط شرط (Over/Under/handicap) را در فیلد line بگذار؛ اگر خط جزئی از pick بود آن را هم در line تکرار کن.
 - اگر ضریب دیده نشد odds=null. اگر بازار را نفهمیدی ولی انتخاب روشن بود، market را با یک کد لاتینِ کوتاه توصیف کن و انتخاب را کامل در pick بنویس.
 - اگر هیچ تیپ صریحی نبود، آرایهٔ خالی [] بده."""

# Legacy pluggable-vision prompt (fallback path, image-only, non-Gemini keys).
VISION_PROMPT = """این تصویر یک کوپن/اسلیپ شرط‌بندی فوتبال یا پست پیش‌بینی است (احتمالاً فارسی).
همه تیپ‌های قابل تشخیص را استخراج کن و فقط JSON آرایه‌ای برگردان — هر نوع بازاری (نتیجه، گل، هندیکاپ آسیایی/اروپایی، هر دو گل، نتیجه دقیق، دبل‌شانس، کرنر، کارت، نیمه‌اول/پایان، پراپ بازیکن، ترکیبی):
[{"home":"نام لاتین میزبان","away":"نام لاتین مهمان","market":"کد بازار (مثل 1X2/DC/DNB/OU/BTTS/CS/HTFT/AH/EH/CORN_OU/CARD_OU/HT_1X2/PLAYER_GOAL/COMBO)","pick":"انتخاب کامل","line":"خط مثل 2.5/-1 یا null","odds":1.85}]
قوانین: نام تیم‌ها را به املای لاتین رایج بنویس (پرسپولیس→Persepolis، رئال→Real Madrid).
«برد میزبان»=1X2/1؛ «بالای ۲.۵»=OU/Over/line=2.5؛ «هر دو تیم گل»=BTTS/Yes؛ «شانس دوبل»=DC؛ هندیکاپ→AH با line.
اگر بازار غیرمتعارف بود، یک کد لاتینِ کوتاهِ گویا بساز و انتخاب را کامل در pick بنویس. اگر ضریب نبود odds=null. اگر هیچ تیپی نبود: []"""


def _gemini_key():
    """Gemini API key from the usual aliases.

    Also treats AI_API_KEY as a Gemini key when AI_PROVIDER=gemini OR the key
    looks like a Google key ("AIza…") — so the slip reader works even if the user
    set only AI_API_KEY and left AI_PROVIDER unset (the common misconfig)."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "YT_API_KEY"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    ai = os.environ.get("AI_API_KEY", "").strip()
    if ai and (os.environ.get("AI_PROVIDER", "").strip().lower() == "gemini"
               or ai.startswith("AIza")):
        return ai
    return ""


def _parse_json_array(text):
    a, b = text.find("["), text.rfind("]")
    try:
        items = json.loads(text[a:b + 1]) if a >= 0 <= b else []
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _gemini_extract(text, images):
    """One multimodal Gemini call over a message's text + its images.

    images: list of (mime_type, raw_bytes). Returns a list of raw tip dicts, or
    None when no Gemini key is configured (caller then tries other layers)."""
    key = _gemini_key()
    if not key:
        return None
    model = (os.environ.get("YT_MODEL") or os.environ.get("AI_MODEL")
             or "gemini-2.0-flash").strip()
    parts = [{"text": EXTRACT_PROMPT}]
    if text:
        parts.append({"text": "متن پیام:\n" + text[:4000]})
    for mime, raw in images:
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.b64encode(raw).decode()}})
    try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        r = requests.post(url, timeout=90, json={
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.1,
                                 "responseMimeType": "application/json"}})
        r.raise_for_status()
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_json_array(txt)
    except Exception as ex:
        sys.stderr.write(f"gemini extract failed: {str(ex)[:140]}\n")
        return []


def _vision_extract(img_b64):
    """Fallback: read a bet-slip image with a pluggable non-Gemini AI (vision).

    Returns None when no AI key is configured (so the caller can count it as
    skipped), else a list (possibly empty)."""
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        return None
    provider = os.environ.get("AI_PROVIDER", "openai").strip().lower()
    vmodel = os.environ.get("AI_VISION_MODEL", "").strip() or {
        "openrouter": "nvidia/nemotron-nano-12b-v2-vl:free",
        "gemini": os.environ.get("AI_MODEL", "gemini-2.0-flash") or "gemini-2.0-flash",
        "openai": "gpt-4o-mini",
    }.get(provider, "gpt-4o-mini")
    try:
        if provider == "gemini":
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{vmodel}:generateContent?key={key}")
            r = requests.post(url, timeout=60, json={
                "contents": [{"parts": [
                    {"text": VISION_PROMPT},
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]}],
                "generationConfig": {"temperature": 0.1}})
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            base = ("https://openrouter.ai/api/v1/chat/completions" if provider == "openrouter"
                    else "https://api.openai.com/v1/chat/completions")
            r = requests.post(base, timeout=60,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": vmodel, "temperature": 0.1, "messages": [{
                                  "role": "user", "content": [
                                      {"type": "text", "text": VISION_PROMPT},
                                      {"type": "image_url", "image_url": {
                                          "url": f"data:image/jpeg;base64,{img_b64}"}}]}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        return _parse_json_array(text)
    except Exception as ex:
        sys.stderr.write(f"vision failed: {str(ex)[:120]}\n")
        return []


def _groq_vision_extract(text, images):
    """Groq vision reader — the fallback when Gemini fails / is incomplete.

    Keyed on GROQ_API_KEY. Groq exposes an OpenAI-compatible endpoint, so we POST
    text + image data-URIs as a single user turn. images: list of
    (mime_type, raw_bytes). Returns a list of raw tip dicts, or None when
    GROQ_API_KEY is not set (caller then has no fallback left)."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    model = (os.environ.get("GROQ_VISION_MODEL", "").strip()
             or "llama-3.2-11b-vision-preview")
    content = [{"type": "text", "text": EXTRACT_PROMPT}]
    if text:
        content.append({"type": "text", "text": "متن پیام:\n" + text[:4000]})
    for mime, raw in images:
        b64 = base64.b64encode(raw).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}})
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          timeout=90,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "temperature": 0.1,
                                "messages": [{"role": "user", "content": content}]})
        r.raise_for_status()
        return _parse_json_array(r.json()["choices"][0]["message"]["content"])
    except Exception as ex:
        sys.stderr.write(f"groq vision failed: {str(ex)[:140]}\n")
        return []


# Markets the downstream model understands; pick vocab per market for sanity checks.
KNOWN_MARKETS = {"1X2", "DC", "OU05", "OU15", "OU25", "OU35", "OU45",
                 "BTTS", "CS", "AH", "DNB"}
_PICK_VOCAB = {
    "1X2": {"1", "X", "2"},
    "DC": {"1X", "X2", "12"},
    "BTTS": {"YES", "NO"},
}
_OU_PICKS = {"OVER", "UNDER"}


# Markets we understand *strictly* (fixed pick vocab). Anything outside this set
# is still accepted as a custom market (see _validate_tip) as long as it carries a
# non-empty selection — we normalize it rather than reject it, so exotic markets
# (corners, cards, player props, combos, half-time variants…) are never lost.
KNOWN_MARKETS = {"1X2", "DC", "DNB", "OU05", "OU15", "OU25", "OU35", "OU45",
                 "BTTS", "CS", "AH", "EH", "HTFT"}
_PICK_VOCAB = {
    "1X2": {"1", "X", "2"},
    "DC": {"1X", "X2", "12"},
    "DNB": {"1", "2"},
    "BTTS": {"YES", "NO"},
}
_OU_PICKS = {"OVER", "UNDER"}

# Normalize free-form / localized market labels to our canonical codes. The value
# may be a plain code, or a (code, forced_pick) tuple when the label also fixes the
# selection (e.g. "over 2.5" → OU25/Over). Longest keys are matched first.
_MARKET_ALIASES = {
    "1x2": "1X2", "match result": "1X2", "full time result": "1X2", "ftr": "1X2",
    "moneyline": "1X2", "ml": "1X2", "wdw": "1X2", "نتیجه": "1X2", "برنده": "1X2",
    "double chance": "DC", "dc": "DC", "دبل شانس": "DC", "شانس دوبل": "DC",
    "draw no bet": "DNB", "dnb": "DNB", "بدون تساوی": "DNB",
    "both teams to score": "BTTS", "btts": "BTTS", "gg/ng": "BTTS", "gg": ("BTTS", "Yes"),
    "ng": ("BTTS", "No"), "هر دو گل": "BTTS", "هر دو تیم گل": "BTTS",
    "correct score": "CS", "cs": "CS", "نتیجه دقیق": "CS",
    "over/under": "OU25", "o/u": "OU25", "totals": "OU25", "total goals": "OU25",
    "goals over/under": "OU25", "زیر/بالای گل": "OU25",
    "asian handicap": "AH", "ah": "AH", "هندیکاپ آسیایی": "AH", "handicap": "AH",
    "european handicap": "EH", "eh": "EH", "3-way handicap": "EH",
    "ht/ft": "HTFT", "htft": "HTFT", "half time/full time": "HTFT", "نیمه/پایان": "HTFT",
    "corners": "CORN_OU", "corner": "CORN_OU", "total corners": "CORN_OU", "کرنر": "CORN_OU",
    "cards": "CARD_OU", "card": "CARD_OU", "total cards": "CARD_OU", "کارت": "CARD_OU",
    "bookings": "CARD_OU", "offsides": "OFF_OU", "shots": "SHOT_OU",
    "anytime goalscorer": "PLAYER_GOAL", "goalscorer": "PLAYER_GOAL", "گلزن": "PLAYER_GOAL",
    "anytime scorer": "PLAYER_GOAL", "assist": "PLAYER_ASSIST", "پاس گل": "PLAYER_ASSIST",
    "bet builder": "COMBO", "same game multi": "COMBO", "combo": "COMBO", "ترکیبی": "COMBO",
}
# A tolerant matcher for any code we can't map explicitly but that still looks
# like a real market code (letters, digits, _ / - . and spaces).
_CUSTOM_MARKET_RE = None  # compiled lazily in _validate_tip


def _canon_market(raw_market, raw_pick):
    """Return (market_code, forced_pick_or_None). Maps aliases → canonical codes;
    folds an over/under line embedded in the label into OU-with-line; leaves an
    already-canonical or reasonable custom code as-is (uppercased)."""
    import re
    m = str(raw_market or "").strip()
    if not m:
        return "1X2", None
    low = m.lower()
    # exact alias hit
    if low in _MARKET_ALIASES:
        val = _MARKET_ALIASES[low]
        return (val, None) if isinstance(val, str) else val
    # "over 2.5" / "under 1.5" style folded into OU<line>
    mo = re.fullmatch(r"(over|under)\s*([0-9]+(?:\.[0-9])?)", low)
    if mo:
        line = mo.group(2).replace(".", "")
        return f"OU{line if len(line) > 1 else line + '5'}", mo.group(1).capitalize()
    # substring alias (label carries extra words)
    for k, val in _MARKET_ALIASES.items():
        if k in low:
            return (val, None) if isinstance(val, str) else val
    # otherwise treat as a custom code, normalized to UPPER_SNAKE
    code = re.sub(r"[^0-9A-Za-z]+", "_", m).strip("_").upper()
    return (code or "CUSTOM"), None


def _validate_tip(it):
    """Sanity-check one raw tip. Returns (normalized_dict, "") or (None, reason).

    Rigor without over-restriction: both team names must be present, distinct and
    plausible, and there must be a concrete selection. Markets are normalized
    (aliases → canonical codes); for the CORE markets we enforce the exact pick
    vocabulary, but ANY other valid market (corners, cards, player props, combos,
    half-time variants, custom codes…) is accepted and normalized rather than
    dropped. Odds — when present — must be a decimal in a realistic range.
    Rejections carry a specific Persian reason so bad data is never registered."""
    import re
    home = str(it.get("home") or "").strip()
    away = str(it.get("away") or "").strip()
    pick = str(it.get("pick") or "").strip()
    market, forced = _canon_market(it.get("market"), pick)
    if forced and not pick:
        pick = forced
    elif forced:
        pick = forced  # label fixes the selection (e.g. "over 2.5")

    if not home or not away:
        return None, "نام هر دو تیم لازم است"
    if home.lower() == away.lower():
        return None, f"دو تیم یکسان‌اند ({home})"
    if len(home) < 2 or len(away) < 2 or len(home) > 40 or len(away) > 40:
        return None, "نام تیم نامعتبر"
    if not any(c.isalpha() for c in home) or not any(c.isalpha() for c in away):
        return None, "نام تیم نامعتبر"
    if not pick:
        return None, "انتخاب مشخص نیست"

    line = it.get("line")
    line = None if line in (None, "", "null", "None") else str(line).strip()

    # Strict pick vocab ONLY for the core markets; custom markets pass through.
    pu = pick.upper()
    if market in _PICK_VOCAB and pu not in _PICK_VOCAB[market]:
        return None, f"انتخاب نامعتبر برای {market}: {pick}"
    if market.startswith("OU") and market in KNOWN_MARKETS and pu not in _OU_PICKS:
        return None, f"انتخاب اوور/آندر نامعتبر: {pick}"
    if market == "CS" and not re.fullmatch(r"\d{1,2}-\d{1,2}", pick):
        return None, f"نتیجهٔ دقیق نامعتبر: {pick}"
    if market == "HTFT" and not re.fullmatch(r"[1X2]/[1X2]", pu):
        return None, f"انتخاب نیمه/پایان نامعتبر: {pick}"
    # For any custom/extended market, the market code itself must look sane.
    if market not in KNOWN_MARKETS and not re.fullmatch(r"[0-9A-Z][0-9A-Z_./-]{0,23}", market):
        return None, f"کد بازار نامعتبر: {market}"

    odds = it.get("odds")
    if odds not in (None, "", "null", "None"):
        try:
            odds = float(str(odds).replace(",", "."))
        except (TypeError, ValueError):
            return None, f"ضریب نامعتبر: {it.get('odds')}"
        if not (1.01 <= odds <= 100.0):
            return None, f"ضریب خارج از محدودهٔ منطقی: {odds}"
    else:
        odds = None

    return {"home": home[:40], "away": away[:40], "market": market,
            "pick": pick[:60], "line": line, "odds": odds,
            "league": (str(it.get("league") or "").strip() or None),
            "prob": it.get("prob"), "note": str(it.get("note") or "").strip()[:120]}, ""


def read_slip(text, images):
    """Read a bet slip (text + images) with Gemini→Groq fallback + validation.

    images: list of (mime_type, raw_bytes). Returns (tips, rejections, meta):
      tips       — validated tip dicts (see _validate_tip)
      rejections — list of (raw_item, reason) for tips that failed sanity checks
      meta       — {"reader": "gemini"|"groq"|None, ...}

    Primary = Gemini multimodal. Falls back to Groq vision when Gemini has no
    key, errors, or yields nothing usable (missing teams/pick)."""
    def _usable(items):
        return bool(items) and any(
            (i.get("home") and i.get("away") and i.get("pick")) for i in (items or []))

    raw, reader = None, None
    if _gemini_key():
        raw, reader = _gemini_extract(text or "", images), "gemini"

    # Fallback to Groq when Gemini is absent or came back empty/incomplete.
    if not _usable(raw) and os.environ.get("GROQ_API_KEY"):
        alt = _groq_vision_extract(text or "", images)
        if _usable(alt) or raw is None:
            raw, reader = alt, "groq"

    if raw is None:
        return [], [], {"reader": None, "error": "no Gemini/Groq key configured"}

    tips, rejects = [], []
    for it in raw[:20]:
        norm, reason = _validate_tip(it)
        (tips if norm else rejects).append(norm if norm else (it, reason))
    return tips, rejects, {"reader": reader}


def _raw_to_tip(it, origin, is_image, match_date):
    """Canonical tip dict from a raw Gemini/vision item (merge_rank canonizes)."""
    home = str(it.get("home") or "").strip()
    away = str(it.get("away") or "").strip()
    pick = str(it.get("pick") or "").strip()
    if not home or not away or not pick or home.lower() == away.lower():
        return None
    prob = it.get("prob")
    try:
        prob = float(prob)
        prob = prob if 0.0 < prob <= 1.0 else None
    except (TypeError, ValueError):
        prob = None
    odds = it.get("odds")
    try:
        odds = float(odds)
        odds = odds if 1.01 <= odds <= 30 else None
    except (TypeError, ValueError):
        odds = None
    league = str(it.get("league") or "").strip() or None
    note = str(it.get("note") or "").strip()[:120] or (
        "استخراج از تصویر" if is_image else "استخراج از متن")
    return {"source": f"tg:{origin}{' (عکس)' if is_image else ''}",
            "source_type": "telegram_personal", "lang": "fa", "league": league,
            "home": home[:40], "away": away[:40], "match_date": match_date,
            "market": str(it.get("market") or "1X2"), "pick": pick,
            "line": it.get("line"),
            "prob": prob, "odds": odds, "note": note, "url": None}


def _save_photo(base, token, photo_sizes):
    """Download the largest photo size via file_id → persist under data/tg_media/.

    Returns (path, mime, raw_bytes) or (None, None, None) on failure. Re-uses an
    already-saved file (keyed by Telegram's file_unique_id) without re-download."""
    try:
        best = photo_sizes[-1]  # largest resolution
        fid = best["file_id"]
        uid = best.get("file_unique_id") or fid
        os.makedirs(MEDIA, exist_ok=True)
        path = os.path.join(MEDIA, f"{uid}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as f:
                return path, "image/jpeg", f.read()
        fp = requests.get(f"{base}/getFile", params={"file_id": fid},
                          timeout=20).json()["result"]["file_path"]
        raw = requests.get(
            f"https://api.telegram.org/file/bot{token}/{fp}", timeout=30).content
        if not raw or len(raw) > 8_000_000:
            return None, None, None
        with open(path, "wb") as f:
            f.write(raw)
        return path, "image/jpeg", raw
    except Exception as ex:
        sys.stderr.write(f"photo save failed: {str(ex)[:100]}\n")
        return None, None, None


def _origin_of(msg):
    """Best-effort origin name for source attribution (forwarded or direct)."""
    fo = msg.get("forward_origin") or {}
    if fo.get("chat", {}).get("title"):
        return fo["chat"]["title"]
    if fo.get("sender_user", {}).get("first_name"):
        return fo["sender_user"]["first_name"]
    if msg.get("forward_from_chat", {}).get("title"):  # older API shape
        return msg["forward_from_chat"]["title"]
    return "فوروارد شخصی"


def run():
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        print("SKIP: TG_BOT_TOKEN not set (bot inbox needs it)")
        return
    base = f"https://api.telegram.org/bot{token}"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
    have_gemini = bool(_gemini_key())

    tips, sources, manifest = [], {}, []
    offset, pages = None, 0
    photos_saved = photos_skipped = rejected_total = 0
    readers_used = set()

    while pages < 10:  # up to ~1000 updates
        pages += 1
        params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{base}/getUpdates", params=params, timeout=25)
            updates = r.json().get("result", [])
        except Exception as ex:
            print(f"getUpdates failed: {str(ex)[:120]}")
            break
        if not updates:
            break

        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            ts = datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc)
            if ts < cutoff:
                continue
            origin = _origin_of(msg)
            mdate = str(ts.date())
            text = msg.get("text") or msg.get("caption") or ""

            # 1) download + persist any photo (bet slip / stats / form)
            images, photo_paths = [], []
            if msg.get("photo"):
                if photos_saved >= MAX_PHOTOS:
                    photos_skipped += 1
                else:
                    path, mime, raw = _save_photo(base, token, msg["photo"])
                    if path:
                        photos_saved += 1
                        images.append((mime, raw))
                        photo_paths.append(os.path.relpath(path, HERE))
                    else:
                        photos_skipped += 1

            found = []
            # 2) PRIMARY Gemini → Groq fallback + validation (shared with the bot)
            valids, rejects, meta = read_slip(text, images)
            if meta.get("reader"):
                readers_used.add(meta["reader"])
            for v in valids:
                t = _raw_to_tip(v, origin, is_image=bool(images) and not text, match_date=mdate)
                if t:
                    found.append(t)
            if rejects:
                rejected_total += len(rejects)
                sys.stderr.write(f"[tg_inbox] {origin}: {len(rejects)} tip(s) rejected — "
                                 + "; ".join(r[1] for r in rejects[:3]) + "\n")

            # 3) free, conservative regex net over the text (complements the AI reader)
            if text:
                found += extract_tips(text, source=f"tg:{origin}",
                                      source_type="telegram_personal",
                                      match_date=mdate)

            tips.extend(found)
            if found or photo_paths:
                sources[origin] = sources.get(origin, 0) + len(found)
                manifest.append({"date": mdate, "origin": origin,
                                 "text": text[:200], "photos": photo_paths,
                                 "tips": len(found)})

    # dedupe across the run
    seen, out = set(), []
    for t in tips:
        k = (t["source"], t["home"].lower(), t["away"].lower(), t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tips_tg_inbox.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "tg_inbox_manifest.json"), "w") as f:
        json.dump({"generated": str(datetime.now(timezone.utc)),
                   "messages": manifest}, f, ensure_ascii=False, indent=1)

    hint = ""
    if photos_saved and not have_gemini and not os.environ.get("GROQ_API_KEY") \
            and not os.environ.get("AI_API_KEY"):
        hint = ("photos saved but not read — set GEMINI_API_KEY or GROQ_API_KEY "
                "for vision")
    if readers_used:
        reader = "+".join(sorted(readers_used))
    elif have_gemini:
        reader = "gemini"
    elif os.environ.get("GROQ_API_KEY"):
        reader = "groq"
    elif os.environ.get("AI_API_KEY"):
        reader = "pluggable"
    else:
        reader = "regex-only"
    print(json.dumps({"forwarded_tips": len(out), "channels": sources,
                      "photos_saved": photos_saved, "photos_skipped": photos_skipped,
                      "rejected": rejected_total, "reader": reader,
                      "hint": hint}, ensure_ascii=False))


if __name__ == "__main__":
    run()
    sys.exit(0)
