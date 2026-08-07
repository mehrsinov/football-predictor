# -*- coding: utf-8 -*-
"""Quick-check bot responder: forward a slip -> get probabilities back.

Runs on a frequent GitHub Actions cron. Flow:
 1. Fast gate (requests only): poll bot getUpdates; exit in seconds if empty.
 2. For new forwarded messages (text or slip photos via pluggable vision AI),
    extract the betting options.
 3. Analyze each leg with the Dixon-Coles engine (probability + fair odds +
    market-odds verdict when the slip shows odds) and the combined accumulator
    probability of the whole slip.
 4. Reply DIRECTLY to the sender's chat via the bot.
 5. Append extracted tips to output/tips_tg_inbox.json (pruned to 48h) so the
    morning deep-report counts them as sources too.

Honesty rules: model probabilities are calibrated estimates, never guarantees;
replies always carry the disclaimer line.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
MAX_PHOTOS = 10

FA = {
    ("1X2", "1"): "برد میزبان", ("1X2", "X"): "مساوی", ("1X2", "2"): "برد مهمان",
    ("DC", "1X"): "میزبان نمی‌بازد", ("DC", "X2"): "مهمان نمی‌بازد", ("DC", "12"): "مساوی نمی‌شود",
    ("OU15", "Over"): "بالای ۱.۵ گل", ("OU15", "Under"): "زیر ۱.۵ گل",
    ("OU25", "Over"): "بالای ۲.۵ گل", ("OU25", "Under"): "زیر ۲.۵ گل",
    ("OU35", "Over"): "بالای ۳.۵ گل", ("OU35", "Under"): "زیر ۳.۵ گل",
    ("BTTS", "Yes"): "هر دو تیم گل می‌زنند", ("BTTS", "No"): "هر دو گل نمی‌زنند",
}


def _collect_updates(base):
    """Consume pending updates; return list of {chat_id, origin, text, photos, date}."""
    msgs, offset, pages = [], None, 0
    while pages < 10:
        pages += 1
        params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        try:
            updates = requests.get(f"{base}/getUpdates", params=params, timeout=20).json().get("result", [])
        except Exception:
            break
        if not updates:
            break
        for u in updates:
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            origin = "کوپن شما"
            fo = m.get("forward_origin") or {}
            if fo.get("chat", {}).get("title"):
                origin = fo["chat"]["title"]
            elif m.get("forward_from_chat", {}).get("title"):
                origin = m["forward_from_chat"]["title"]
            msgs.append({"chat_id": m.get("chat", {}).get("id"),
                         "origin": origin,
                         "text": m.get("text") or m.get("caption") or "",
                         "photo_id": (m.get("photo") or [{}])[-1].get("file_id"),
                         "date": m.get("date", 0)})
    return msgs


def _send(base, chat_id, text):
    for i in range(0, len(text), 3800):
        try:
            requests.post(f"{base}/sendMessage",
                          json={"chat_id": chat_id, "text": text[i:i + 3800],
                                "disable_web_page_preview": True}, timeout=20)
        except Exception:
            pass


def _analyze_leg(tip, cache):
    """Return (line_fa, prob or None). Uses analyze_match per unique fixture."""
    from analyze_match import analyze
    key = (tip["home"].lower(), tip["away"].lower())
    if key not in cache:
        cache[key] = analyze(tip["home"], tip["away"])
    res = cache[key]
    label = FA.get((tip["market"], tip["pick"]), f"{tip['market']} {tip['pick']}")
    if res.get("error"):
        return f"❓ {tip['home']} — {tip['away']} | {label}: تیم/لیگ در پوشش مدل نبود", None
    opt = next((o for o in res["options"]
                if o["market"] == tip["market"] and str(o["pick"]) == str(tip["pick"])), None)
    if opt is None:
        return f"❓ {res['match']} | {label}: این مارکت در منوی مدل نبود", None
    p, fair = opt["p"], opt["fair_odds"]
    emoji = "🟢" if p >= 0.6 else ("🟡" if p >= 0.45 else "🔴")
    line = f"{emoji} {res['match']} | {label}\n   احتمال مدل: {round(p*100)}٪ | ضریب منصفانه: {fair}"
    if tip.get("odds"):
        try:
            o = float(tip["odds"])
            ev = p * o - 1
            line += f" | ضریب کوپن: {o} ({'ارزش دارد 💎' if ev >= 0.05 else 'ارزش خاصی ندارد' if ev > -0.1 else 'ضعیف'})"
        except (TypeError, ValueError):
            pass
    return line, p


def run():
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        print("SKIP: no TG_BOT_TOKEN")
        return
    base = f"https://api.telegram.org/bot{token}"
    msgs = [m for m in _collect_updates(base) if m["chat_id"]]
    if not msgs:
        print("no new messages — fast exit")
        return
    print(f"{len(msgs)} new message(s) — full analysis starting")

    # heavy imports only now
    from tg_tip_extract import extract_tips
    from tg_bot_inbox import _vision_extract  # reuses AI_* env
    import base64 as b64

    # make sure data exists (bootstrap on cold cache)
    import run_daily as rd
    import glob as g
    if not g.glob(os.path.join(rd.RAW, "extra_*.csv")):
        rd.refresh_fixtures(); rd.refresh_history(bootstrap=True)
    else:
        rd.refresh_fixtures()

    all_tips, photos_done = [], 0
    cache = {}
    for m in msgs:
        tips = []
        if m["text"]:
            tips += extract_tips(m["text"], source=f"tg:{m['origin']}",
                                 source_type="telegram_personal",
                                 match_date=str(datetime.now(timezone.utc).date()))
        if m["photo_id"] and photos_done < MAX_PHOTOS:
            try:
                fp = requests.get(f"{base}/getFile", params={"file_id": m["photo_id"]},
                                  timeout=20).json()["result"]["file_path"]
                img = requests.get(f"https://api.telegram.org/file/bot{token}/{fp}", timeout=30).content
                items = _vision_extract(b64.b64encode(img).decode()) if len(img) <= 4_000_000 else []
                if items is None:
                    _send(base, m["chat_id"],
                          "📸 عکس رسید ولی خواندن تصویر نیاز به کلید AI دارد (Secrets: AI_PROVIDER/AI_API_KEY/AI_MODEL).")
                    items = []
                else:
                    photos_done += 1
                for it in items[:12]:
                    if it.get("home") and it.get("away") and it.get("pick"):
                        tips.append({"source": f"tg:{m['origin']} (عکس)", "source_type": "telegram_personal",
                                     "lang": "fa", "league": None,
                                     "home": str(it["home"])[:40], "away": str(it["away"])[:40],
                                     "match_date": str(datetime.now(timezone.utc).date()),
                                     "market": str(it.get("market") or "1X2"), "pick": str(it["pick"]),
                                     "prob": None, "odds": it.get("odds"),
                                     "note": "کوپن تصویری", "url": None})
            except Exception as ex:
                sys.stderr.write(f"photo err: {str(ex)[:100]}\n")

        if not tips:
            if m["text"] or m["photo_id"]:
                _send(base, m["chat_id"],
                      "پیامت رسید ولی تیپ قابل تشخیصی پیدا نکردم 🤔 (دو تیم + انتخاب لازم است)")
            continue

        # analyze legs
        lines, probs = [], []
        for t in tips[:10]:
            line, p = _analyze_leg(t, cache)
            lines.append(line)
            if p:
                probs.append(p)
        reply = [f"⚡ تحلیل فوری کوپن ({m['origin']}):", ""]
        reply += lines
        if len(probs) >= 2:
            comb = 1.0
            for p in probs:
                comb *= p
            reply += ["", f"🎲 شانس کل کوپن ({len(probs)} پایه): {round(comb*100)}٪"
                          f" | ضریب منصفانه کل: {round(1/comb, 2)}"]
        elif probs:
            reply += ["", f"شانس موفقیت: {round(probs[0]*100)}٪"]
        reply += ["", "⚠️ این‌ها احتمال کالیبره‌اند نه تضمین. حداکثر ۱-۲٪ بانک."]
        _send(base, m["chat_id"], "\n".join(reply))
        all_tips.extend(tips)

    # merge into the daily inbox file (pruned to 48h) so morning report sees them
    path = os.path.join(OUT, "tips_tg_inbox.json")
    old = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                old = json.load(f)
        except Exception:
            old = []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).date().isoformat()
    merged, seen = [], set()
    for t in old + all_tips:
        if (t.get("match_date") or "9999") < cutoff:
            continue
        k = (t["source"], t["home"].lower(), t["away"].lower(), t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            merged.append(t)
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"analyzed & replied | tips stored: {len(merged)}")


if __name__ == "__main__":
    run()
    sys.exit(0)
