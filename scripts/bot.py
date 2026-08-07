# -*- coding: utf-8 -*-
"""پیش‌بین فوتبال — ربات تلگرام تعاملی کامل (long-polling، دکمه‌های شیشه‌ای).

یک ربات تحلیل‌گر تمام‌عیار: منوی اینلاین، تحلیل زنده هر بازی، چک کوپن (متن و
عکس)، پیشنهادهای روز با دلیل، میکس‌ها، رتبه‌بندی کانال‌ها، تنظیمات ضریب حداقل،
و لایه AI افزودنی. فقط با requests کار می‌کند (بدون وابستگی سنگین) و هرجا
پایتون اجرا شود کار می‌کند: هاست رایگان، کامپیوتر شخصی، یا سرور.

اجرا:  TG_BOT_TOKEN=... python3 bot.py
اعتبارنامه‌ها از env: TG_BOT_TOKEN (لازم)، ADMIN_CHAT_ID (اختیاری، محدودسازی)،
AI_PROVIDER/AI_API_KEY/AI_MODEL (اختیاری، برای خواندن عکس کوپن).
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
STATE_PATH = os.path.join(OUT, "bot_state.json")

TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
ADMIN = os.environ.get("ADMIN_CHAT_ID", "").strip()  # if set, only this chat is served

MARKET_FA = {
    ("1X2", "1"): "برد میزبان", ("1X2", "X"): "مساوی", ("1X2", "2"): "برد مهمان",
    ("DC", "1X"): "میزبان نمی‌بازد", ("DC", "X2"): "مهمان نمی‌بازد", ("DC", "12"): "مساوی نمی‌شود",
    ("OU15", "Over"): "بالای ۱.۵ گل", ("OU25", "Over"): "بالای ۲.۵ گل",
    ("OU25", "Under"): "زیر ۲.۵ گل", ("OU35", "Over"): "بالای ۳.۵ گل",
    ("BTTS", "Yes"): "هر دو تیم گل می‌زنند", ("BTTS", "No"): "هر دو گل نمی‌زنند",
}
DISC = "⚠️ احتمال کالیبره است، نه تضمین. مدیریت سرمایه: ۱-۲٪ بانک."

# readiness of the analysis data (first boot prepares it in the background so
# the bot answers instantly instead of staying silent for minutes)
READY = {"ok": False, "note": "در حال آماده‌سازی داده‌ها (بار اول ۵-۱۰ دقیقه)…"}
BUSY = {"refresh": False}

# ------------------------------------------------------------------ state
def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "offset": None}


def save_state(s):
    os.makedirs(OUT, exist_ok=True)
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


def user_cfg(state, cid):
    return state["users"].setdefault(str(cid), {"min_odds": 1.0, "mode": None})


# ------------------------------------------------------------- telegram io
def api(method, **payload):
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=40)
        return r.json()
    except Exception as ex:
        sys.stderr.write(f"api {method} err: {str(ex)[:120]}\n")
        return {}


def send(cid, text, kb=None, edit=None):
    payload = {"chat_id": cid, "text": text, "disable_web_page_preview": True}
    if kb:
        payload["reply_markup"] = {"inline_keyboard": kb}
    if edit:
        payload["message_id"] = edit
        return api("editMessageText", **payload)
    return api("sendMessage", **payload)


def btn(text, data):
    return {"text": text, "callback_data": data}


def url_btn(text, url):
    return {"text": text, "url": url}


# --------------------------------------------------------------- menus/ui
def main_menu():
    return [
        [btn("🎯 پیشنهادهای امروز", "today"), btn("🎲 میکس‌های منطقی", "mixes")],
        [btn("⚽ تحلیل یک بازی", "analyze"), btn("🧾 چک کوپن من", "check")],
        [btn("📰 گزارش کامل روز", "report"), btn("📈 بازی‌های امروز", "fixtures:0")],
        [btn("📊 کانال‌های من", "channels"), btn("⚙️ تنظیمات", "settings")],
        [btn("ℹ️ راهنما", "help")],
    ]


def needs_data(cid, mid=None):
    """When data isn't ready yet, answer immediately instead of silence."""
    if READY["ok"]:
        return False
    send(cid, f"⏳ {READY['note']}\n\nمنو کار می‌کند؛ چند دقیقه دیگر دوباره امتحان کن — "
              f"به‌محض آماده‌شدن، همه‌چیز آنی جواب می‌دهد.",
         kb=[[btn("🏠 منو", "menu")]], edit=mid)
    return True


def view_report(cid, mid=None):
    p = os.path.join(OUT, "report_fa.md")
    if not os.path.exists(p):
        return send(cid, "گزارش امروز هنوز ساخته نشده.", kb=[[btn("🏠 منو", "menu")]], edit=mid)
    with open(p, encoding="utf-8") as f:
        text = f.read().replace("**", "").replace("# ", "").replace("## ", "")
    for i in range(0, len(text), 3800):
        send(cid, text[i:i + 3800])
    send(cid, "پایان گزارش 📰", kb=[[btn("🏠 منو", "menu")]])


def do_refresh(cid):
    """Admin on-demand data refresh (fixtures+model+picks+report), threaded."""
    if BUSY["refresh"]:
        return send(cid, "⏳ یک بروزرسانی از قبل در حال اجراست…")
    def work():
        BUSY["refresh"] = True
        try:
            send(cid, "🔄 بروزرسانی شروع شد (۲-۴ دقیقه)…")
            import run_daily as rd
            rd.refresh_fixtures()
            for mod in ("predict_today", "merge_rank", "webapp_data", "picks", "report_gen"):
                m = __import__(mod)
                (getattr(m, "run", None) or getattr(m, "build"))()
            send(cid, "✅ داده‌ها تازه شد! پیشنهادها و تحلیل‌ها الان با آخرین ضرایب و برنامه بازی‌هاست.",
                 kb=[[btn("🎯 پیشنهادهای امروز", "today"), btn("🏠 منو", "menu")]])
        except Exception as ex:
            send(cid, f"⚠️ بروزرسانی ناقص ماند: {str(ex)[:150]}")
        finally:
            BUSY["refresh"] = False
    threading.Thread(target=work, daemon=True).start()


def home_text():
    d = read_json("curated_picks.json", {})
    meta = read_json("webapp_data.json", {}).get("meta", {})
    n = len(d.get("picks", []))
    line = f"📅 {meta.get('date_fa','')} · {meta.get('n_tips',0)} تیپ از منابع مختلف"
    return (f"⚽ *پیش‌بین فوتبال* — دستیار تحلیل تو\n\n{line}\n"
            f"امروز {n} پیشنهاد برگزیده با دلیل آماده‌ست.\n\nیکی رو انتخاب کن 👇")


def read_json(name, default):
    p = os.path.join(OUT, name)
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def fa_mkt(m, p):
    return MARKET_FA.get((m, p), f"{m} {p}")


def pct(x):
    try:
        return f"{round(float(x)*100)}٪"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------- features
def view_today(cid, mid=None):
    d = read_json("curated_picks.json", {})
    picks = d.get("picks", [])
    if not picks:
        return send(cid, "امروز پیک برگزیده‌ای نداریم (یا هنوز گزارش ساخته نشده). بعداً امتحان کن.",
                    kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    lines = ["🎯 *پیشنهادهای برگزیده امروز*\n"]
    kb = []
    for i, p in enumerate(picks[:10], 1):
        odds = f" | ضریب {p['odds']}" if p.get("odds") else ""
        lines.append(f"{i}. {p['home']} — {p['away']}\n   {p['label_fa']} · {pct(p['p'])}{odds}"
                     f"{' 💎' if p.get('value') else ''}")
        kb.append([btn(f"{i}. {p['label_fa']} ({pct(p['p'])})", f"pick:{i-1}")])
    kb.append([btn("⬅️ منو", "menu")])
    send(cid, "\n".join(lines), kb=kb, edit=mid)


def view_pick(cid, idx, mid=None):
    d = read_json("curated_picks.json", {})
    picks = d.get("picks", [])
    if idx >= len(picks):
        return view_today(cid, mid)
    p = picks[idx]
    ai = read_json("ai_brief.json", {}).get("picks", {}).get(str(p.get("fixture_id")))
    out = [f"⚽ *{p['home']} — {p['away']}*",
           f"🏆 {p['league']}{' · ' + p['time'] if p.get('time') else ''}", "",
           f"🎯 *{p['label_fa']}* — احتمال *{pct(p['p'])}*"
           + (f" | ضریب {p['odds']}" if p.get("odds") else "")
           + (" 💎 ولیو" if p.get("value") else ""), "", "*چرا؟*"]
    out += [f"◂ {r}" for r in p.get("reasons", [])]
    if p.get("sources"):
        out += ["", f"🤝 منابع: {'، '.join(p['sources'][:5])}"]
    if ai:
        out += ["", f"🤖 {ai}"]
    out += ["", DISC]
    send(cid, "\n".join(out),
         kb=[[btn("⬅️ پیشنهادها", "today"), btn("🏠 منو", "menu")]], edit=mid)


def view_mixes(cid, mid=None):
    d = read_json("curated_picks.json", {})
    mixes = d.get("mixes", [])
    if not mixes:
        return send(cid, "امروز میکس منطقی‌ای ساخته نشد.", kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    out = ["🎲 *میکس‌های منطقی امروز*\n"]
    for m in mixes:
        est = "≈" if m.get("estimated") else ""
        out.append(f"*{m['name']}* — ضریب {est}{m['odds']} · شانس {pct(m['p'])}")
        for l in m["legs"]:
            out.append(f"   ◂ {l['m']}: {l['pick']} ({l['odds']})")
        out.append("")
    out.append(DISC)
    send(cid, "\n".join(out), kb=[[btn("⬅️ منو", "menu")]], edit=mid)


def view_channels(cid, mid=None):
    d = read_json("tg_channel_scores.json", {})
    ch = [c for c in d.get("channels", []) if c.get("settled", 0) >= 8 and c.get("hit_rate") is not None]
    ch.sort(key=lambda c: -c["hit_rate"])
    if not ch:
        return send(cid, "هنوز کانالی رتبه‌بندی نشده. کوپن کانال‌هات رو به من فوروارد کن تا کم‌کم بسنجمشون.",
                    kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    out = ["📊 *رتبه‌بندی کانال‌های تو* (بر اساس نرخ برد واقعی)\n"]
    for c in ch[:12]:
        ao = f" · ضریب میانگین {c['avg_odds']}" if c.get("avg_odds") else ""
        out.append(f"• {c['channel']}: {pct(c['hit_rate'])} ({c['wins']}/{c['settled']}){ao}")
    send(cid, "\n".join(out), kb=[[btn("⬅️ منو", "menu")]], edit=mid)


def view_fixtures(cid, page, mid=None):
    d = read_json("webapp_data.json", {})
    fx = d.get("fixtures", [])
    if not fx:
        return send(cid, "فعلاً بازی‌ای در دسترس نیست.", kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    per = 8
    chunk = fx[page * per:(page + 1) * per]
    out = [f"📈 *بازی‌های امروز* (صفحه {page+1})\n"]
    kb = []
    for f in chunk:
        bl = f.get("blend") or f["model"]
        out.append(f"• {f['home']} — {f['away']}  ({f.get('time','')})\n"
                   f"   {pct(bl['p_home'])}/{pct(bl['p_draw'])}/{pct(bl['p_away'])}")
        kb.append([btn(f"⚽ {f['home']} - {f['away']}", f"fx:{f['id']}")])
    nav = []
    if page > 0:
        nav.append(btn("« قبلی", f"fixtures:{page-1}"))
    if (page + 1) * per < len(fx):
        nav.append(btn("بعدی »", f"fixtures:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([btn("⬅️ منو", "menu")])
    send(cid, "\n".join(out), kb=kb, edit=mid)


# lazy analysis engine (kept warm across calls)
_ENGINE = {}


def analyze_match_cached(home, away, min_odds=1.0):
    from analyze_match import analyze
    return analyze(home, away, min_odds=min_odds)


def view_match_analysis(cid, home, away, min_odds=1.0, value_only=False, mid=None):
    send(cid, "⏳ در حال تحلیل کامل بازی…", edit=mid)
    try:
        res = analyze_match_cached(home, away, min_odds=min_odds)
    except Exception as ex:
        sys.stderr.write("analyze err: " + str(ex) + "\n")
        return send(cid, "خطا در تحلیل. اسم تیم‌ها رو دقیق‌تر بنویس یا بعداً امتحان کن.",
                    kb=[[btn("🏠 منو", "menu")]])
    if res.get("error"):
        return send(cid, f"⚠️ {res['error']}", kb=[[btn("🏠 منو", "menu")]])
    opts = res["options"]
    if value_only:
        opts = [o for o in opts if o.get("value")]
    opts = opts[:16]
    out = [f"⚽ *{res['match']}*", f"🏆 {res['league']} · xG مدل {res['xg']}", ""]
    if not opts:
        out.append("گزینه‌ای با این فیلتر پیدا نشد.")
    for o in opts:
        line = f"• {o['label_fa']}: *{pct(o['p'])}* | منصفانه {o['fair_odds']}"
        if o.get("real_odds"):
            line += f" | بازار {o['real_odds']}"
            if o.get("value"):
                line += " 💎"
        if o.get("n_sources"):
            line += f" | 🤝{o['n_sources']}"
        out.append(line)
    out += ["", DISC]
    kb = [[btn("💎 فقط ولیو", f"mval:{home}|{away}"), btn("📋 همه", f"mall:{home}|{away}")],
          [btn("⬅️ منو", "menu")]]
    send(cid, "\n".join(out), kb=kb)


# slip checking (text + photo) -> probabilities
def check_slip(cid, text=None, photo_id=None):
    from tg_tip_extract import extract_tips
    tips = []
    if text:
        tips += extract_tips(text, source="tg:کوپن شما", source_type="telegram_personal",
                             match_date=str(datetime.now(timezone.utc).date()))
    if photo_id:
        got = read_slip_photo(photo_id)
        if got is None:
            send(cid, "📸 عکس رسید ولی خواندن تصویر نیاز به کلید AI دارد (AI_API_KEY).")
        else:
            tips += got
    if not tips:
        return send(cid, "تیپ قابل تشخیصی پیدا نکردم 🤔 (دو تیم + انتخاب لازم است).",
                    kb=[[btn("🏠 منو", "menu")]])
    out, probs = ["🧾 *تحلیل کوپن تو*\n"], []
    for t in tips[:12]:
        try:
            res = analyze_match_cached(t["home"], t["away"])
        except Exception:
            res = {"error": "x"}
        label = fa_mkt(t["market"], t["pick"])
        if res.get("error"):
            out.append(f"❓ {t['home']} — {t['away']} | {label}: خارج از پوشش مدل")
            continue
        opt = next((o for o in res["options"] if o["market"] == t["market"]
                    and str(o["pick"]) == str(t["pick"])), None)
        if not opt:
            out.append(f"❓ {res['match']} | {label}: این مارکت در منو نبود")
            continue
        p = opt["p"]
        probs.append(p)
        em = "🟢" if p >= 0.6 else ("🟡" if p >= 0.45 else "🔴")
        line = f"{em} {res['match']} | {label}: *{pct(p)}* (منصفانه {opt['fair_odds']})"
        if t.get("odds"):
            try:
                ev = p * float(t["odds"]) - 1
                line += f" | کوپن {t['odds']} {'💎' if ev >= 0.05 else ''}"
            except (TypeError, ValueError):
                pass
        out.append(line)
    if len(probs) >= 2:
        comb = 1.0
        for p in probs:
            comb *= p
        out += ["", f"🎲 *شانس کل کوپن*: {pct(comb)} | ضریب منصفانه کل: {round(1/comb,2)}"]
    elif probs:
        out += ["", f"شانس موفقیت: *{pct(probs[0])}*"]
    out += ["", DISC]
    send(cid, "\n".join(out), kb=[[btn("🏠 منو", "menu")]])


def read_slip_photo(photo_id):
    try:
        from tg_bot_inbox import _vision_extract
        fp = api("getFile", file_id=photo_id).get("result", {}).get("file_path")
        if not fp:
            return []
        img = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fp}", timeout=30).content
        if len(img) > 4_000_000:
            return []
        items = _vision_extract(base64.b64encode(img).decode())
        if items is None:
            return None
        out = []
        for it in items[:12]:
            if it.get("home") and it.get("away") and it.get("pick"):
                out.append({"home": str(it["home"])[:40], "away": str(it["away"])[:40],
                            "market": str(it.get("market") or "1X2"), "pick": str(it["pick"]),
                            "odds": it.get("odds")})
        return out
    except Exception as ex:
        sys.stderr.write("slip photo err: " + str(ex)[:100] + "\n")
        return []


def view_settings(cid, state, mid=None):
    cfg = user_cfg(state, cid)
    mo = cfg.get("min_odds", 1.0)
    out = f"⚙️ *تنظیمات*\n\nضریب حداقل فعلی برای تحلیل بازی: *{mo}*\nفقط آپشن‌هایی با ضریب منصفانه بالاتر از این نشان داده می‌شوند."
    kb = [[btn("۱.۰ (همه)", "mo:1.0"), btn("۱.۵", "mo:1.5"), btn("۲.۰", "mo:2.0")],
          [btn("۲.۵", "mo:2.5"), btn("۳.۰", "mo:3.0"), btn("۴.۰", "mo:4.0")],
          [btn("⬅️ منو", "menu")]]
    send(cid, out, kb=kb, edit=mid)


def view_help(cid, mid=None):
    out = ("ℹ️ *راهنما*\n\n"
           "🎯 *پیشنهادهای امروز*: پیک‌های برگزیده با دلیل منطقی.\n"
           "⚽ *تحلیل یک بازی*: بزن، بعد اسم دو تیم رو این‌طوری بفرست: `پرسپولیس - استقلال`.\n"
           "🧾 *چک کوپن*: متن یا *عکس* کوپنت رو بفرست، شانس هر پایه + شانس کل رو می‌گم.\n"
           "🎲 *میکس‌ها*: دوبل/سه‌گانه منطقی امروز.\n"
           "📊 *کانال‌های من*: رتبه کانال‌هایی که کوپنشون رو فوروارد کردی.\n"
           "⚙️ *تنظیمات*: ضریب حداقل.\n\n"
           "می‌تونی هر لحظه فقط اسم دو تیم یا یه عکس کوپن بفرستی — بدون منو هم می‌فهمم.\n\n" + DISC)
    send(cid, out, kb=[[btn("🏠 منو", "menu")]], edit=mid)


# --------------------------------------------------------------- routing
def handle_callback(state, cq):
    cid = cq["message"]["chat"]["id"]
    mid = cq["message"]["message_id"]
    data = cq.get("data", "")
    api("answerCallbackQuery", callback_query_id=cq["id"])
    cfg = user_cfg(state, cid)
    data_views = ("today", "mixes", "report")
    if (data in data_views or data.startswith(("fixtures:", "pick:", "fx:", "mval:", "mall:"))) \
            and not READY["ok"]:
        return needs_data(cid, mid)
    if data == "menu":
        send(cid, home_text(), kb=main_menu(), edit=mid)
    elif data == "today":
        view_today(cid, mid)
    elif data == "mixes":
        view_mixes(cid, mid)
    elif data == "report":
        view_report(cid, mid)
    elif data == "refresh":
        do_refresh(cid)
    elif data == "channels":
        view_channels(cid, mid)
    elif data == "help":
        view_help(cid, mid)
    elif data == "settings":
        view_settings(cid, state, mid)
    elif data.startswith("fixtures:"):
        view_fixtures(cid, int(data.split(":")[1]), mid)
    elif data.startswith("pick:"):
        view_pick(cid, int(data.split(":")[1]), mid)
    elif data.startswith("fx:"):
        fid = int(data.split(":")[1])
        d = read_json("webapp_data.json", {})
        f = next((x for x in d.get("fixtures", []) if x["id"] == fid), None)
        if f:
            view_match_analysis(cid, f["home"], f["away"], cfg.get("min_odds", 1.0))
    elif data.startswith("mval:"):
        h, a = data[5:].split("|", 1)
        view_match_analysis(cid, h, a, cfg.get("min_odds", 1.0), value_only=True)
    elif data.startswith("mall:"):
        h, a = data[5:].split("|", 1)
        view_match_analysis(cid, h, a, 1.0, value_only=False)
    elif data == "analyze":
        cfg["mode"] = "await_match"
        send(cid, "اسم دو تیم رو بفرست، مثل:\n`Flamengo - Palmeiras`\nیا فارسی: `پرسپولیس - استقلال`",
             kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    elif data == "check":
        cfg["mode"] = "await_slip"
        send(cid, "متن کوپن یا *عکس* کوپنت رو بفرست تا شانسش رو حساب کنم 🧾",
             kb=[[btn("⬅️ منو", "menu")]], edit=mid)
    elif data.startswith("mo:"):
        cfg["min_odds"] = float(data.split(":")[1])
        view_settings(cid, state, mid)
    save_state(state)


def handle_message(state, msg):
    cid = msg["chat"]["id"]
    cfg = user_cfg(state, cid)
    text = (msg.get("text") or msg.get("caption") or "").strip()
    photo_id = (msg.get("photo") or [{}])[-1].get("file_id")

    if text.startswith("/start") or text == "/menu":
        cfg["mode"] = None
        save_state(state)
        note = "" if READY["ok"] else f"\n\n⏳ {READY['note']}"
        return send(cid, home_text() + note, kb=main_menu())
    if text.startswith("/help"):
        return view_help(cid)
    if text.startswith("/report"):
        return needs_data(cid) or view_report(cid)
    if text.startswith("/refresh"):
        return do_refresh(cid)
    if text.startswith("/today"):
        return needs_data(cid) or view_today(cid)

    # anything needing the model waits politely while data prepares
    if not READY["ok"] and (photo_id or text):
        return needs_data(cid)

    # photo anywhere -> treat as slip
    if photo_id:
        cfg["mode"] = None
        save_state(state)
        return check_slip(cid, text=text or None, photo_id=photo_id)

    # explicit "team - team" -> analyze, regardless of mode
    sep = next((s for s in (" - ", " – ", " vs ", "-", "–") if s in text), None)
    looks_match = sep and len(text) <= 60 and len(text.split(sep)) == 2 and all(part.strip() for part in text.split(sep))

    if cfg.get("mode") == "await_slip":
        cfg["mode"] = None
        save_state(state)
        return check_slip(cid, text=text)
    if cfg.get("mode") == "await_match" or looks_match:
        cfg["mode"] = None
        save_state(state)
        if not sep:
            return send(cid, "اسم دو تیم رو با خط تیره بفرست: `تیم۱ - تیم۲`")
        h, a = [p.strip() for p in text.split(sep, 1)]
        return view_match_analysis(cid, h, a, cfg.get("min_odds", 1.0))

    # fallback: if it has 2+ team-like lines, try slip; else menu
    if len(text.splitlines()) >= 2:
        return check_slip(cid, text=text)
    send(cid, "سلام رفیق ⚽ از منو استفاده کن یا مستقیم اسم دو تیم / عکس کوپن بفرست.",
         kb=main_menu())


def ensure_data():
    """Prepare data + today's artifacts as a SEPARATE PROCESS.

    Critical: the pipeline fits ~35 league models (CPU-bound). Running it in a
    thread would hold the GIL and freeze the polling loop (bot goes silent).
    A subprocess runs on its own core, so the bot stays responsive throughout.
    """
    have_report = os.path.exists(os.path.join(OUT, "report_fa.md"))
    args = [sys.executable, os.path.join(HERE, "run_daily.py")]
    if have_report:
        args.append("--fast")   # report already there -> just refresh fixtures/model
    try:
        subprocess.run(args, cwd=HERE, timeout=1500,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        sys.stderr.write("prep warn:\n" + traceback.format_exc())


def main():
    if not TOKEN:
        print("ERROR: TG_BOT_TOKEN لازم است."); sys.exit(1)
    me = api("getMe").get("result", {})
    print(f"bot @{me.get('username','?')} up. polling…")

    # prepare data in the BACKGROUND so the bot replies from second one
    def prep():
        try:
            ensure_data()
        finally:
            READY["ok"] = True
            READY["note"] = ""
            print("data ready ✅")
    threading.Thread(target=prep, daemon=True).start()

    state = load_state()
    api("setMyCommands", commands=[{"command": "start", "description": "منوی اصلی"},
                                   {"command": "today", "description": "پیشنهادهای امروز"},
                                   {"command": "help", "description": "راهنما"}])
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])}
            if state.get("offset") is not None:
                params["offset"] = state["offset"]
            r = requests.get(f"{API}/getUpdates", params=params, timeout=60)
            for u in r.json().get("result", []):
                state["offset"] = u["update_id"] + 1
                try:
                    if ADMIN:
                        who = ((u.get("message") or u.get("callback_query", {}).get("message") or {})
                               .get("chat", {}).get("id"))
                        if who and str(who) != ADMIN:
                            continue
                    if "callback_query" in u:
                        handle_callback(state, u["callback_query"])
                    elif "message" in u:
                        handle_message(state, u["message"])
                except Exception:
                    sys.stderr.write(traceback.format_exc())
                save_state(state)
        except Exception:
            sys.stderr.write("poll err:\n" + traceback.format_exc())
            time.sleep(3)


if __name__ == "__main__":
    main()
