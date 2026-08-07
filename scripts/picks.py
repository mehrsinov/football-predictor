# -*- coding: utf-8 -*-
"""Curated picks with logical Persian reasons — the 'assistant' layer.

Filters the day's options down to the ones worth a human's attention:
  keep if (>=2 independent sources agree) OR (model blend >= 0.65 with known teams)
  max ONE pick per match, ranked by probability. Cap 12.

For every kept pick, builds data-driven Persian reasons: recent form, attack/
defence strength vs league average, model xG, H2H patterns (market-aware),
injuries, source consensus, market value, and YouTube summary notes when present.

Also builds 2-3 logical mixes (double/triple) from odds-bearing picks.

Inputs : output/ranked_options.json, output/webapp_data.json
Output : output/curated_picks.json
"""
import json
import math
import os

from staking import kelly_fraction, parlay_kelly, stake_units

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

MAIN_MARKETS = {"1X2", "DC", "OU15", "OU25", "OU35", "BTTS", "DNB"}

FA = {
    ("1X2", "1"): "برد میزبان", ("1X2", "X"): "مساوی", ("1X2", "2"): "برد مهمان",
    ("DC", "1X"): "میزبان نمی‌بازد", ("DC", "X2"): "مهمان نمی‌بازد", ("DC", "12"): "مساوی نمی‌شود",
    ("OU15", "Over"): "بالای ۱.۵ گل", ("OU15", "Under"): "زیر ۱.۵ گل",
    ("OU25", "Over"): "بالای ۲.۵ گل", ("OU25", "Under"): "زیر ۲.۵ گل",
    ("OU35", "Over"): "بالای ۳.۵ گل", ("OU35", "Under"): "زیر ۳.۵ گل",
    ("BTTS", "Yes"): "هر دو تیم گل می‌زنند", ("BTTS", "No"): "هر دو تیم گل نمی‌زنند",
}


def fa_label(m, p):
    return FA.get((m, p), f"{m} {p}")


def _load(name, default):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _wdl(form):
    w = sum(1 for x in form if x["res"] == "W")
    d = sum(1 for x in form if x["res"] == "D")
    l = sum(1 for x in form if x["res"] == "L")
    return w, d, l


def _avg_total(form):
    tots = []
    for x in form:
        try:
            a, b = x["score"].split("-")
            tots.append(int(a) + int(b))
        except Exception:
            pass
    return round(sum(tots) / len(tots), 1) if tots else None


def _h2h_stats(h2h):
    n = len(h2h or [])
    if not n:
        return None
    overs = btts = 0
    for m in h2h:
        try:
            a, b = (int(x) for x in m["score"].split("-"))
            overs += (a + b) >= 3
            btts += (a > 0 and b > 0)
        except Exception:
            n -= 1
    return {"n": n, "over": overs, "btts": btts} if n else None


def _pct(x):
    return f"{round((math.exp(x) - 1) * 100):+d}٪"


def reasons_for(opt, fx, th, ta):
    m, pk = opt["market"], opt["pick"]
    R = []
    home, away = fx["home"], fx["away"]
    xh, xa = fx["model"].get("xg_home"), fx["model"].get("xg_away")

    # --- market-specific core logic
    if m in ("1X2", "DC", "DNB"):
        side = "1" if pk in ("1", "1X") else ("2" if pk in ("2", "X2") else "X")
        fav, oth = (home, away) if side == "1" else (away, home)
        tf, to = (th, ta) if side == "1" else (ta, th)
        if tf.get("form"):
            w, d, l = _wdl(tf["form"])
            R.append(f"فرم {fav}: {w} برد و {d} مساوی در ۵ بازی اخیر")
        if side != "X" and xh is not None:
            edge = (xh, xa) if side == "1" else (xa, xh)
            if edge[0] and edge[1] and edge[0] > edge[1] * 1.25:
                R.append(f"برتری xG مدل: {edge[0]} در برابر {edge[1]}")
        if tf.get("atk") is not None and abs(tf["atk"]) > 0.12:
            R.append(f"قدرت حمله {fav} حدود {_pct(tf['atk'])} نسبت به میانگین لیگ")
        if to.get("form"):
            w2, d2, l2 = _wdl(to["form"])
            if l2 >= 3:
                R.append(f"{oth} در ۵ بازی اخیر {l2} باخت داشته")
        hh = fx.get("h2h") or []
        if side == "1" and len(hh) >= 3:
            hw = sum(1 for x in hh if x["home"] == home and
                     int(x["score"].split("-")[0]) > int(x["score"].split("-")[1]))
            if hw >= 2:
                R.append(f"در دیدارهای رودررو، {home} میزبانِ برتر بوده ({hw} برد خانگی اخیر)")
    elif m in ("OU15", "OU25", "OU35"):
        line = {"OU15": 1.5, "OU25": 2.5, "OU35": 3.5}[m]
        tot_model = round((xh or 0) + (xa or 0), 1)
        if pk == "Over":
            if tot_model >= line + 0.3:
                R.append(f"مجموع xG مدل {tot_model} — بالاتر از خط {line}")
            at_h, at_a = _avg_total(th.get("form") or []), _avg_total(ta.get("form") or [])
            if at_h and at_a and (at_h + at_a) / 2 >= line + 0.4:
                R.append(f"میانگین گلِ بازی‌های اخیر دو تیم: {at_h} و {at_a}")
        else:
            if tot_model <= line - 0.3:
                R.append(f"مجموع xG مدل فقط {tot_model} — پایین‌تر از خط {line}")
            at_h, at_a = _avg_total(th.get("form") or []), _avg_total(ta.get("form") or [])
            if at_h and at_a and (at_h + at_a) / 2 <= line - 0.3:
                R.append(f"بازی‌های اخیر دو تیم کم‌گل بوده (میانگین {at_h} و {at_a})")
        hs = _h2h_stats(fx.get("h2h"))
        if hs and hs["n"] >= 3:
            r = hs["over"] / hs["n"]
            if pk == "Over" and r >= 0.6:
                R.append(f"در {hs['n']} رودرروی اخیر، {hs['over']} بازی بالای ۲.۵ گل بوده")
            if pk == "Under" and r <= 0.4:
                R.append(f"در {hs['n']} رودرروی اخیر فقط {hs['over']} بازی بالای ۲.۵ گل بوده")
    elif m == "BTTS":
        hs = _h2h_stats(fx.get("h2h"))
        if pk == "Yes":
            if xh and xa and min(xh, xa) >= 0.9:
                R.append(f"هر دو تیم xG قابل‌توجه دارند ({xh} و {xa})")
            if hs and hs["n"] >= 3 and hs["btts"] / hs["n"] >= 0.6:
                R.append(f"در {hs['n']} رودرروی اخیر، {hs['btts']} بار هر دو گل زدند")
        else:
            if xa is not None and xa <= 0.8:
                R.append(f"حمله مهمان کم‌رمق است (xG {xa})")
            if hs and hs["n"] >= 3 and hs["btts"] / hs["n"] <= 0.4:
                R.append(f"فقط {hs['btts']} از {hs['n']} رودرروی اخیر «هر دو گل» شد")

    # --- generic evidence
    inj = fx.get("injuries") or {}
    if m in ("1X2", "DC", "DNB"):
        side_fav_home = pk in ("1", "1X")
        inj_oth = inj.get("away" if side_fav_home else "home")
        if inj_oth and inj_oth >= 3:
            R.append(f"حریف {inj_oth} مصدوم/محروم دارد 🚑")
    n_src = opt.get("n_sources") or 0
    if n_src >= 2:
        names = "، ".join((opt.get("source_names") or [])[:4])
        R.append(f"اجماع {n_src} منبع مستقل: {names}")
    if opt.get("ev") is not None and opt["ev"] >= 0.05 and opt.get("value_flag"):
        R.append(f"ضریب بازار {opt.get('odds')} بالاتر از ضریب منصفانه → ارزش {round(opt['ev']*100):+d}٪ 💎")
    for nt in (opt.get("notes") or [])[:1]:
        R.append(f"🎥 {nt.get('src')}: {nt.get('note')}")
    return R[:4]


def build():
    ranked = _load("ranked_options.json", {})
    web = _load("webapp_data.json", {})
    fixtures = {(f["home"], f["away"]): f for f in web.get("fixtures", [])}
    teams = web.get("teams", {})

    # Two explicit tiers, consensus FIRST (user: single-source picks are useless):
    #   Tier A — اجماع: >=2 independent sources, p in 52..82% (sorted by sources, then p)
    #   Tier B — مدل قوی: fills remaining slots, p in 62..78% (fair odds >=1.28), known teams
    # A 91% pick at fair odds 1.10 is statistically right but useless for staking.
    tier_a, tier_b = [], []
    for o in ranked.get("options", []):
        if o["market"] not in MAIN_MARKETS:
            continue
        ns = o.get("n_sources") or 0
        if ns >= 2 and 0.52 <= o["blend_p"] <= 0.82:
            tier_a.append(o)
        elif ns < 2 and 0.62 <= o["blend_p"] <= 0.78 and o.get("known_teams"):
            tier_b.append(o)
    tier_a.sort(key=lambda o: (-(o.get("n_sources") or 0), -o["blend_p"]))
    tier_b.sort(key=lambda o: -o["blend_p"])
    cand = tier_a + tier_b

    picks, seen_match, mkt_count = [], set(), {}
    for o in cand:
        key = (o["home"], o["away"])
        if key in seen_match or mkt_count.get(o["market"], 0) >= 4:
            continue
        fx = fixtures.get(key)
        if fx is None:
            continue
        th = (teams.get(o["league"]) or {}).get(o["home"], {})
        ta = (teams.get(o["league"]) or {}).get(o["away"], {})
        R = reasons_for(o, fx, th, ta)
        if len(R) < 2:      # a pick without enough logic isn't an assistant pick
            continue
        seen_match.add(key)
        mkt_count[o["market"]] = mkt_count.get(o["market"], 0) + 1
        stake = kelly_fraction(o["blend_p"], o.get("odds"))
        picks.append({
            "fixture_id": fx["id"], "league": o["league"], "time": o.get("time") or "",
            "home": o["home"], "away": o["away"],
            "market": o["market"], "pick": o["pick"], "label_fa": fa_label(o["market"], o["pick"]),
            "p": o["blend_p"], "odds": o.get("odds"), "ev": o.get("ev"),
            "value": bool(o.get("value_flag")), "n_sources": o.get("n_sources") or 0,
            "sources": o.get("source_names") or [], "reasons": R,
            "stake": stake,                       # quarter-Kelly fraction of bankroll
            "stake_units": stake_units(o["blend_p"], o.get("odds")),
        })
    picks = picks[:12]

    # logical mixes; when a leg lacks market odds, use fair odds (1/p) as the
    # MINIMUM acceptable price and label the combo estimate accordingly
    legs = [p for p in picks if p["p"] >= 0.60][:6]

    def leg_odds(l):
        return (float(l["odds"]), False) if l.get("odds") else (round(1 / l["p"], 2), True)

    def mk_mix(name, ls):
        prod, est = 1.0, False
        for l in ls:
            o, e = leg_odds(l)
            prod *= o
            est = est or e
        pk = parlay_kelly([l["p"] for l in ls], prod)
        return {"name": name,
                "legs": [{"m": f"{l['home']} — {l['away']}", "pick": l["label_fa"],
                          "odds": leg_odds(l)[0]} for l in ls],
                "odds": round(prod, 2), "estimated": est,
                "p": round(math.prod(l["p"] for l in ls), 3),
                "stake": pk, "stake_units": round(pk * 100.0, 2)}

    mixes = []
    if len(legs) >= 2:
        mixes.append(mk_mix("دوبل منطقی", legs[:2]))
    if len(legs) >= 3:
        mixes.append(mk_mix("سه‌گانه", legs[:3]))

    out = {"picks": picks, "mixes": mixes,
           "note": "فقط گزینه‌های چندمنبعی یا با اطمینان بالای مدل؛ حداکثر یک پیک از هر بازی."}
    with open(os.path.join(OUT, "curated_picks.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"curated picks: {len(picks)} | mixes: {len(mixes)}")
    return out


if __name__ == "__main__":
    build()
