# -*- coding: utf-8 -*-
"""Build the daily Persian report (markdown) from ranked_options.json."""
import json
import os

import jdatetime
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "..", "output")

MARKET_FA = {
    ("1X2", "1"): "برد میزبان", ("1X2", "X"): "مساوی", ("1X2", "2"): "برد مهمان",
    ("DC", "1X"): "شانس دوبل 1X (میزبان نمی‌بازد)", ("DC", "X2"): "شانس دوبل X2 (مهمان نمی‌بازد)",
    ("DC", "12"): "شانس دوبل 12 (مساوی نمی‌شود)",
    ("OU15", "Over"): "بالای ۱.۵ گل", ("OU15", "Under"): "زیر ۱.۵ گل",
    ("OU25", "Over"): "بالای ۲.۵ گل", ("OU25", "Under"): "زیر ۲.۵ گل",
    ("OU35", "Over"): "بالای ۳.۵ گل", ("OU35", "Under"): "زیر ۳.۵ گل",
    ("BTTS", "Yes"): "هر دو تیم گل می‌زنند", ("BTTS", "No"): "هر دو تیم گل نمی‌زنند",
}

LEAGUE_FA = {
    "Brazil Serie A": "سری آ برزیل", "Argentina Liga Profesional": "لیگ حرفه‌ای آرژانتین",
    "Sweden Allsvenskan": "آلسونسکان سوئد", "Norway Eliteserien": "الیت‌سرین نروژ",
    "Finland Veikkausliiga": "ویکاوس‌لیگا فنلاند", "Denmark Superliga": "سوپرلیگ دانمارک",
    "USA MLS": "MLS آمریکا", "Mexico Liga MX": "لیگا MX مکزیک",
    "Japan J1 League": "جی‌لیگ ژاپن", "China Super League": "سوپرلیگ چین",
    "Poland Ekstraklasa": "اکستراکلاسا لهستان", "Romania Superliga": "سوپرلیگا رومانی",
    "Ireland Premier Division": "لیگ برتر ایرلند", "Spain Segunda": "سگوندا اسپانیا",
    "Belgium Pro League": "پرولیگ بلژیک", "Russia Premier League": "لیگ برتر روسیه",
    "Switzerland Super League": "سوپرلیگ سوئیس", "Austria Bundesliga": "بوندسلیگا اتریش",
    "England Premier League": "لیگ برتر انگلیس", "Spain La Liga": "لالیگا اسپانیا",
    "Germany Bundesliga": "بوندسلیگا آلمان", "Italy Serie A": "سری آ ایتالیا",
    "France Ligue 1": "لوشامپیونه فرانسه", "Netherlands Eredivisie": "اردیویسه هلند",
    "Portugal Liga": "لیگ پرتغال", "Turkey Super Lig": "سوپرلیگ ترکیه",
    "Greece Super League": "سوپرلیگ یونان", "Scotland Premiership": "پریمیرشیپ اسکاتلند",
}


def _load_curated():
    p = os.path.join(OUT, "curated_picks.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _load_ai():
    p = os.path.join(OUT, "ai_brief.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _load_channel_scores():
    p = os.path.join(OUT, "tg_channel_scores.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            return json.load(f).get("channels", [])
    except Exception:
        return []


def odds_ok(o):
    v = o.get("odds")
    try:
        return v is not None and float(v) > 1.0 and float(v) == float(v)
    except (TypeError, ValueError):
        return False


def fa_market(market, pick):
    if market == "CS":
        return f"نتیجه دقیق {pick}"
    if market == "AH":
        return f"هندیکپ {pick}"
    return MARKET_FA.get((market, pick), f"{market} {pick}")


def fa_league(lg):
    return LEAGUE_FA.get(lg, lg or "—")


def tier(p):
    if p >= 0.70:
        return "A+"
    if p >= 0.62:
        return "A"
    if p >= 0.55:
        return "B"
    return "C"


def pct(p):
    return f"{round(p * 100)}٪"


def line_for(o, i):
    parts = [f"**{i}. {o['home']} — {o['away']}**  ({fa_league(o['league'])}"]
    if o.get("time"):
        parts[0] += f"، {o['time']}"
    parts[0] += ")"
    l2 = f"   🎯 {fa_market(o['market'], o['pick'])} | احتمال: **{pct(o['blend_p'])}**"
    if odds_ok(o):
        l2 += f" | ضریب: **{o['odds']}**"
    if odds_ok(o) and o.get("ev") is not None:
        l2 += f" | ارزش: {'+' if o['ev'] >= 0 else ''}{round(o['ev'] * 100)}٪"
    l2 += f" | سطح: {tier(o['blend_p'])}"
    if o.get("value_flag"):
        l2 += " 💎"
    if not o.get("known_teams", True):
        l2 += " ⚠️(داده تیم کم)"
    # source attribution — which sources recommended this pick (user can verify)
    names = o.get("source_names") or []
    if names:
        shown = "، ".join(names[:5])
        more = f" +{len(names) - 5}" if len(names) > 5 else ""
        l3s = f"   📣 پیشنهاد شده در ({o['n_sources']} منبع): {shown}{more}"
    else:
        l3s = "   📣 منبع خارجی: — (فقط مدل آماری)"
    l3 = f"   xG مدل: {o['xg']} | محتمل‌ترین نتایج: " + \
         "، ".join(f"{s['score']} ({pct(s['p'])})" for s in o["top_scores"][:2])
    inj = o.get("injuries") or {}
    if inj:
        bits = []
        if inj.get("home"):
            bits.append(f"میزبان {inj['home']}")
        if inj.get("away"):
            bits.append(f"مهمان {inj['away']}")
        if bits:
            l3 += " | 🚑 مصدوم/محروم: " + "، ".join(bits)
    return "\n".join([parts[0], l2, l3s, l3])


def build():
    with open(os.path.join(OUT, "ranked_options.json")) as f:
        data = json.load(f)
    bt = {}
    p = os.path.join(OUT, "backtest_results.json")
    if os.path.exists(p):
        with open(p) as f:
            bt = json.load(f)

    now = pd.Timestamp.now(tz="Asia/Tehran")
    jd = jdatetime.datetime.fromgregorian(datetime=now.tz_localize(None).to_pydatetime())
    weekday_fa = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"][jd.weekday() - 2 if jd.weekday() >= 2 else jd.weekday() + 5]
    months_fa = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    date_fa = f"{weekday_fa} {jd.day} {months_fa[jd.month - 1]} {jd.year}"

    opts = data["options"]
    # dedupe: max 2 options per match in top list, prefer higher rank
    seen = {}
    mkt_count = {}
    top = []
    for o in opts:
        k = (o["home"], o["away"])
        seen[k] = seen.get(k, 0) + 1
        mkt_count[o["market"]] = mkt_count.get(o["market"], 0)
        if seen[k] <= 2 and o["blend_p"] >= 0.52 and mkt_count[o["market"]] < 5:
            top.append(o)
            mkt_count[o["market"]] += 1
        if len(top) >= 10:
            break

    values = [o for o in opts if o.get("value_flag")]
    values.sort(key=lambda o: -(o["ev"] or 0))
    consensus = sorted([o for o in opts if o["n_sources"] >= 2],
                       key=lambda o: (-o["n_sources"], -o["blend_p"]))[:6]

    L = []
    L.append(f"# ⚽ گزارش تحلیل و پیش‌بینی فوتبال")
    L.append(f"**{date_fa}** ({now.strftime('%Y-%m-%d')})")
    L.append("")
    L.append(f"📊 {data['n_options_model']} گزینه تحلیل‌شده روی بازی‌های تحت پوشش مدل | "
             f"{data['n_tips_total']} تیپ جمع‌آوری‌شده از منابع خارجی "
             f"({data['n_tips_matched']} تیپ با بازی‌های مدل تطبیق داده شد)")
    # data-source transparency line
    srcs = data.get("sources_used")
    if srcs:
        L.append("")
        L.append("🔎 منابع امروز: " + srcs)
    tgp = data.get("telegram_personal")
    if tgp:
        L.append(f"📲 تلگرام شخصی تو: {tgp}")
    L.append("")
    # assistant layer: curated picks with reasons (falls back to raw top list)
    curated = _load_curated()
    if curated and curated.get("picks"):
        ai = _load_ai()
        if ai and ai.get("daily_brief"):
            L.append(f"🤖 **جمع‌بندی:** {ai['daily_brief']}")
            L.append("")
        L.append("## 🎯 پیشنهادهای برگزیده روز (با دلیل)")
        L.append("")
        for i, p in enumerate(curated["picks"], 1):
            hdr = f"**{i}. {p['home']} — {p['away']}**  ({fa_league(p['league'])}{'، ' + p['time'] if p['time'] else ''})"
            l2 = f"   🎯 **{p['label_fa']}** | احتمال: **{pct(p['p'])}**"
            if p.get("odds"):
                l2 += f" | ضریب: **{p['odds']}**"
            if p.get("value"):
                l2 += " 💎"
            L.append(hdr)
            L.append(l2)
            for r in p["reasons"]:
                L.append(f"   ◂ {r}")
            if ai and ai.get("picks", {}).get(str(p["fixture_id"])):
                L.append(f"   🤖 {ai['picks'][str(p['fixture_id'])]}")
            L.append("")
        for mx in curated.get("mixes", []):
            legs = " + ".join(f"{l['m']} ({l['pick']})" for l in mx["legs"])
            L.append(f"**🎲 {mx['name']}:** {legs} | ضریب ترکیبی: **{mx['odds']}** | احتمال: {pct(mx['p'])}")
            L.append("")
        L.append(f"_{curated.get('note','')}_")
        L.append("")
    else:
        L.append("## 🏆 گزینه‌های برتر روز")
        L.append("")
        for i, o in enumerate(top, 1):
            L.append(line_for(o, i))
            L.append("")
    L.append("## 💎 ولیو بت‌ها (اختلاف مثبت با بازار)")
    L.append("")
    if values:
        for i, o in enumerate(values[:5], 1):
            L.append(line_for(o, i))
            L.append("")
    else:
        L.append("امروز هیچ گزینه‌ای از فیلترهای سخت‌گیرانه ولیو (ارزش ≥۵٪، احتمال ≥۲۸٪، ضریب ≤۶، اختلاف معقول مدل-بازار) عبور نکرد — و این یعنی سیستم صادقه، نه خراب. روزهایی که ولیو واقعی باشد اینجا می‌بینی.")
        L.append("")
    if consensus:
        L.append("## 🤝 بیشترین اجماع تیپسترها")
        L.append("")
        for i, o in enumerate(consensus, 1):
            srcs = "، ".join(o["source_names"][:4])
            L.append(f"{i}. {o['home']} — {o['away']}: **{fa_market(o['market'], o['pick'])}** "
                     f"({o['n_sources']} منبع: {srcs}) | احتمال مدل: {pct(o['blend_p'])}")
        L.append("")

    # combos from safest distinct-match picks with real odds
    combo_pool = [o for o in opts if odds_ok(o) and o["blend_p"] >= 0.58]
    used = set()
    legs = []
    for o in combo_pool:
        k = (o["home"], o["away"])
        if k not in used:
            legs.append(o)
            used.add(k)
    if len(legs) >= 2:
        L.append("## 🎲 پیشنهاد ترکیبی")
        L.append("")
        two = legs[:2]
        p2 = two[0]["blend_p"] * two[1]["blend_p"]
        o2 = two[0]["odds"] * two[1]["odds"]
        L.append(f"**دوبل امن:** " + " + ".join(f"{x['home']}–{x['away']} ({fa_market(x['market'], x['pick'])})" for x in two)
                 + f" | ضریب ترکیبی: {o2:.2f} | احتمال: {pct(p2)}")
        if len(legs) >= 3:
            three = legs[:3]
            p3 = three[0]["blend_p"] * three[1]["blend_p"] * three[2]["blend_p"]
            o3 = three[0]["odds"] * three[1]["odds"] * three[2]["odds"]
            L.append("")
            L.append(f"**سه‌گانه:** " + " + ".join(f"{x['home']}–{x['away']} ({fa_market(x['market'], x['pick'])})" for x in three)
                     + f" | ضریب ترکیبی: {o3:.2f} | احتمال: {pct(p3)}")
        L.append("")

    if data.get("tips_only"):
        L.append("## 📡 تیپ‌های خارج از پوشش مدل (فقط اجماع منابع)")
        L.append("")
        for t in data["tips_only"][:6]:
            if t["n_sources"] >= 2 or (t.get("site_probs_avg") or 0) >= 0.6:
                pr = f" | احتمال اعلامی منابع: {pct(t['site_probs_avg'])}" if t.get("site_probs_avg") else ""
                L.append(f"- {t['home']} — {t['away']}: **{fa_market(t['market'], t['pick'])}** ({t['n_sources']} منبع{pr})")
        L.append("")

    # telegram channel scorecard (if the validator has run)
    ch = _load_channel_scores()
    if ch:
        graded = [c for c in ch if c.get("settled", 0) >= 8 and c.get("hit_rate") is not None]
        graded.sort(key=lambda c: c["hit_rate"], reverse=True)
        if graded:
            L.append("## 🧠 رتبه‌بندی کانال‌های تلگرام تو (بر اساس نرخ برد واقعی)")
            L.append("")
            for c in graded[:6]:
                ao = f"، ضریب میانگین {c['avg_odds']}" if c.get("avg_odds") else ""
                L.append(f"- {c['channel']}: نرخ برد **{pct(c['hit_rate'])}** "
                         f"({c['wins']}/{c['settled']} تیپ سنجیده‌شده{ao})")
            L.append("")
            weak = [c for c in graded if c["hit_rate"] < 0.45]
            if weak:
                L.append(f"⚠️ کانال‌های کم‌بازده (کمتر از ۴۵٪): "
                         + "، ".join(c["channel"] for c in weak[:5]) + " — با احتیاط دنبال کن.")
                L.append("")

    # honest footer
    L.append("---")
    L.append("### 📏 شفافیت عملکرد (بک‌تست واقعی، بدون گلچین)")
    if bt:
        acc = bt.get("model_pick_accuracy")
        mk = bt.get("market_pick_accuracy")
        n = bt.get("n_total")
        L.append(f"- بک‌تست {bt.get('window', '')} روی {n} بازی از {bt.get('leagues')} لیگ: "
                 f"دقت انتخاب مدل {pct(acc)} مقابل {pct(mk)} سوگیری بازار.")
        for thr in (70, 65, 60):
            k = f"acc_at_conf_{thr}"
            if k in bt:
                L.append(f"- گزینه‌های با اطمینان ≥{thr}٪: دقت واقعی {pct(bt[k]['accuracy'])} در {bt[k]['n']} بازی.")
        L.append(f"- دقت جهت اور/آندر ۲.۵: {pct(bt.get('ou25_pick_accuracy', 0))}.")
    L.append("- هیچ پیش‌بینی‌ای قطعی نیست؛ اعداد بالا احتمال کالیبره‌اند نه قول. مدیریت سرمایه: حداکثر ۱–۲٪ بانک روی هر گزینه، از دنبال‌کردن باخت (chasing) پرهیز کن.")

    report = "\n".join(L)
    with open(os.path.join(OUT, "report_fa.md"), "w") as f:
        f.write(report)
    print(report[:1500])
    print(f"\n... [{len(report)} chars total] -> output/report_fa.md")


if __name__ == "__main__":
    build()
