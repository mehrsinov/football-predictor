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
{"home":"نام لاتین تیم میزبان","away":"نام لاتین تیم مهمان","league":"نام لیگ به انگلیسی در صورت ذکر","market":"1X2|DC|OU15|OU25|OU35|BTTS|CS|AH","pick":"انتخاب","prob":عدد۰تا۱یاnull,"odds":عددیاnull,"note":"خلاصهٔ یک‌جمله‌ای فارسی از دلیل"}

قواعد pick بر حسب market:
 - 1X2 → "1" (برد میزبان) / "X" (مساوی) / "2" (برد مهمان)
 - DC  → "1X" یا "X2" یا "12"   |   OU25/OU15/OU35 → "Over" یا "Under"
 - BTTS → "Yes" یا "No"          |   CS → نتیجهٔ دقیق مثل "2-1"
نام تیم‌ها را حتماً لاتین و کامل بنویس (پرسپولیس→Persepolis، رئال→Real Madrid).
«برد میزبان»=1؛ «بالای ۲.۵»=OU25/Over؛ «هر دو تیم گل»=BTTS/Yes؛ «شانس دوبل»=DC.
اگر ضریب دیده نشد odds=null. اگر هیچ تیپ صریحی نبود، آرایهٔ خالی [] بده."""

# Legacy pluggable-vision prompt (fallback path, image-only, non-Gemini keys).
VISION_PROMPT = """این تصویر یک کوپن/اسلیپ شرط‌بندی فوتبال یا پست پیش‌بینی است (احتمالاً فارسی).
همه تیپ‌های قابل تشخیص را استخراج کن و فقط JSON آرایه‌ای برگردان:
[{"home":"نام لاتین تیم میزبان","away":"نام لاتین تیم مهمان","market":"1X2|DC|OU15|OU25|OU35|BTTS|CS|AH","pick":"1|X|2|1X|X2|12|Over|Under|Yes|No|2-1","odds":1.85}]
قوانین: نام تیم‌ها را به املای لاتین رایج بنویس (پرسپولیس→Persepolis، رئال→Real Madrid).
«برد میزبان»=pick 1؛ «بالای ۲.۵»=OU25/Over؛ «هر دو تیم گل»=BTTS/Yes؛ «شانس دوبل»=DC.
اگر ضریب دیده نمی‌شود odds را null بگذار. اگر هیچ تیپی قابل تشخیص نیست: []"""


def _gemini_key():
    """Gemini API key from the usual aliases (AI_API_KEY only if provider=gemini)."""
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"].strip()
    if os.environ.get("GOOGLE_API_KEY"):
        return os.environ["GOOGLE_API_KEY"].strip()
    if os.environ.get("YT_API_KEY"):
        return os.environ["YT_API_KEY"].strip()
    if os.environ.get("AI_PROVIDER", "").strip().lower() == "gemini":
        return os.environ.get("AI_API_KEY", "").strip()
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
             or "gemini-2.5-flash").strip()
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
        "gemini": os.environ.get("AI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
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
    photos_saved = photos_skipped = 0

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
            # 2) PRIMARY: Gemini multimodal — reads text AND images together
            g = _gemini_extract(text, images)
            if g is not None:  # Gemini key present
                for it in g:
                    t = _raw_to_tip(it, origin, is_image=bool(images) and not text, match_date=mdate)
                    if t:
                        found.append(t)
            elif images:  # no Gemini key → legacy pluggable vision per image
                for _mime, raw in images:
                    items = _vision_extract(base64.b64encode(raw).decode())
                    for it in (items or [])[:12]:
                        t = _raw_to_tip(it, origin, is_image=True, match_date=mdate)
                        if t:
                            found.append(t)

            # 3) free, conservative regex net over the text (complements Gemini)
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
    if photos_saved and not have_gemini and not os.environ.get("AI_API_KEY"):
        hint = "photos saved but not read — set a Gemini key (GEMINI_API_KEY) for vision"
    print(json.dumps({"forwarded_tips": len(out), "channels": sources,
                      "photos_saved": photos_saved, "photos_skipped": photos_skipped,
                      "reader": "gemini" if have_gemini else ("pluggable" if os.environ.get("AI_API_KEY") else "regex-only"),
                      "hint": hint}, ensure_ascii=False))


if __name__ == "__main__":
    run()
    sys.exit(0)
