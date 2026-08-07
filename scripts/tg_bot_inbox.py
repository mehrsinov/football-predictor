# -*- coding: utf-8 -*-
"""Bot-inbox harvester — personal channel tips WITHOUT my.telegram.org.

The user simply FORWARDS interesting posts from their favourite channels to
their own bot (the same bot that delivers the daily report). This script reads
the bot's pending updates via getUpdates, extracts betting tips from forwarded
posts (multilingual), tags each tip with the ORIGIN channel name, and writes
them to output/tips_tg_inbox.json — merge_rank picks the file up automatically.

Zero extra credentials: uses TG_BOT_TOKEN only. Runs on GitHub Actions
(api.telegram.org unreachable from the platform sandbox — that's fine).

Notes:
- Telegram keeps unconsumed updates for ~24h, so forward posts any time during
  the day; the morning run will process them.
- Consuming is atomic per run: we advance the offset as we read.
"""
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from tg_tip_extract import extract_tips

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
MAX_PHOTOS = 15  # per run, keeps AI usage tiny

VISION_PROMPT = """این تصویر یک کوپن/اسلیپ شرط‌بندی فوتبال یا پست پیش‌بینی است (احتمالاً فارسی).
همه تیپ‌های قابل تشخیص را استخراج کن و فقط JSON آرایه‌ای برگردان:
[{"home":"نام لاتین تیم میزبان","away":"نام لاتین تیم مهمان","market":"1X2|DC|OU15|OU25|OU35|BTTS|CS|AH","pick":"1|X|2|1X|X2|12|Over|Under|Yes|No|2-1","odds":1.85}]
قوانین: نام تیم‌ها را به املای لاتین رایج بنویس (پرسپولیس→Persepolis، رئال→Real Madrid).
«برد میزبان»=pick 1؛ «بالای ۲.۵»=OU25/Over؛ «هر دو تیم گل»=BTTS/Yes؛ «شانس دوبل»=DC.
اگر ضریب دیده نمی‌شود odds را null بگذار. اگر هیچ تیپی قابل تشخیص نیست: []"""


def _vision_extract(img_b64):
    """Read a bet-slip image with the user's pluggable AI (vision). Fail-soft."""
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        return None  # signals: no vision available
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
        a, b = text.find("["), text.rfind("]")
        items = json.loads(text[a:b + 1]) if a >= 0 <= b else []
        return items if isinstance(items, list) else []
    except Exception as ex:
        sys.stderr.write(f"vision failed: {str(ex)[:120]}\n")
        return []


def run():
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        print("SKIP: TG_BOT_TOKEN not set (bot inbox needs it)")
        return
    base = f"https://api.telegram.org/bot{token}"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)

    tips, sources, offset, pages = [], {}, None, 0
    photos_done = photos_skipped = 0
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
            # origin channel/user name for source attribution
            origin = "فوروارد شخصی"
            fo = msg.get("forward_origin") or {}
            if fo.get("chat", {}).get("title"):
                origin = fo["chat"]["title"]
            elif fo.get("sender_user", {}).get("first_name"):
                origin = fo["sender_user"]["first_name"]
            elif msg.get("forward_from_chat", {}).get("title"):  # older API shape
                origin = msg["forward_from_chat"]["title"]

            found = []
            # 1) text / caption
            text = msg.get("text") or msg.get("caption") or ""
            if text:
                found += extract_tips(text, source=f"tg:{origin}",
                                      source_type="telegram_personal",
                                      match_date=str(ts.date()))
            # 2) photo (bet-slip screenshots — read with pluggable vision AI)
            if msg.get("photo"):
                if photos_done >= MAX_PHOTOS:
                    photos_skipped += 1
                else:
                    try:
                        fid = msg["photo"][-1]["file_id"]  # largest size
                        fp = requests.get(f"{base}/getFile", params={"file_id": fid},
                                          timeout=20).json()["result"]["file_path"]
                        img = requests.get(
                            f"https://api.telegram.org/file/bot{token}/{fp}", timeout=30).content
                        if len(img) <= 4_000_000:
                            items = _vision_extract(base64.b64encode(img).decode())
                            if items is None:
                                photos_skipped += 1  # no AI key configured
                            else:
                                photos_done += 1
                                for it in items[:12]:
                                    if it.get("home") and it.get("away") and it.get("pick"):
                                        found.append({
                                            "source": f"tg:{origin} (عکس)",
                                            "source_type": "telegram_personal",
                                            "lang": "fa", "league": None,
                                            "home": str(it["home"])[:40],
                                            "away": str(it["away"])[:40],
                                            "match_date": str(ts.date()),
                                            "market": str(it.get("market") or "1X2"),
                                            "pick": str(it["pick"]),
                                            "prob": None,
                                            "odds": it.get("odds"),
                                            "note": "استخراج از تصویر کوپن",
                                            "url": None})
                    except Exception as ex:
                        sys.stderr.write(f"photo failed: {str(ex)[:100]}\n")
            tips.extend(found)
            if found:
                sources[origin] = sources.get(origin, 0) + len(found)

    # dedupe
    seen, out = set(), []
    for t in tips:
        k = (t["source"], t["home"].lower(), t["away"].lower(), t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tips_tg_inbox.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"forwarded_tips": len(out), "channels": sources,
                      "photos_read": photos_done, "photos_skipped": photos_skipped,
                      "hint": "photos need AI_API_KEY (vision)" if photos_skipped and not photos_done else ""},
                     ensure_ascii=False))


if __name__ == "__main__":
    run()
    sys.exit(0)
