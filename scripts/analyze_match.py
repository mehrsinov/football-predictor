# -*- coding: utf-8 -*-
"""Interactive single-match deep dive.

Focus on ONE match and dump the FULL betting menu: every market with model
probability, fair odds (1/p), real bookmaker odds where available, an EV flag,
and — crucially — which external sources (sites / YouTube / Telegram channels)
recommended each option, so the user can verify them.

Usage:
    python3 analyze_match.py "River Plate" "Barracas Central"
    python3 analyze_match.py "Flamengo" "Palmeiras" --min-odds 1.8
    python3 analyze_match.py --list          # show today's fixtures to pick from

The agent calls this on demand when the user says "focus on match X".
Output: prints a Persian deep-dive AND writes output/match_analysis.json.
Best-effort: tries 1xBet full-market odds for the match (often Cloudflare-blocked;
falls back to fair odds + real football-data odds silently).
"""
import glob
import json
import os
import sys

import pandas as pd

from data_loader import load_history
from dixon_coles import DixonColes, devig_1x2, devig_power
from merge_rank import team_sim, canon_tip, load_tips

try:
    from fixtures_merge import load_all_fixtures
except Exception:
    load_all_fixtures = None
try:
    from xg_ratings import compute_xg_ratings
except Exception:
    compute_xg_ratings = None

OUT = os.path.join(os.path.dirname(__file__), "..", "output")


def _find_fixture(home, away):
    if load_all_fixtures is None:
        return None
    fx = load_all_fixtures(days_ahead=3)
    best, bs = None, 0
    for _, r in fx.iterrows():
        s = min(team_sim(home, r["home"]), team_sim(away, r["away"]))
        if s > bs:
            best, bs = r, s
    return best if bs >= 70 else None


def _sources_for(home, away):
    """Map each (market,pick) to the external sources that recommended it."""
    tips = load_tips()
    idx = {}
    for t in tips:
        if min(team_sim(home, t["home"]), team_sim(away, t["away"])) >= 80:
            key = (t["market"], t["pick"])
            src = t.get("source")
            idx.setdefault(key, {})
            idx[key][src] = idx[key].get(src, 0) + 1
    return idx


def _guess_league(home, away, hist, fixture):
    if fixture is not None and fixture.get("league"):
        return fixture["league"]
    # else find the league where both teams appear most recently
    best, bestn = None, 0
    for lg, g in hist.groupby("league"):
        teams = set(g["home"]) | set(g["away"])
        n = sum(1 for name in (home, away)
                if any(team_sim(name, t) >= 85 for t in teams))
        if n > bestn:
            best, bestn = lg, n
    return best if bestn >= 1 else None


def analyze(home, away, min_odds=1.0):
    hist = load_history()
    fixture = _find_fixture(home, away)
    league = _guess_league(home, away, hist, fixture)
    if not league:
        return {"error": f"لیگ بازی «{home} - {away}» پیدا نشد. اسم تیم‌ها را دقیق‌تر بده."}

    # resolve canonical team names from the league's roster (better matching)
    lg_hist = hist[hist["league"] == league]
    roster = sorted(set(lg_hist["home"]) | set(lg_hist["away"]))

    def resolve(name):
        best, bs = name, 0
        for t in roster:
            s = team_sim(name, t)
            if s > bs:
                best, bs = t, s
        return best if bs >= 80 else name
    home_c, away_c = resolve(home), resolve(away)

    ref = pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize() + pd.Timedelta(days=1)
    xr = None
    if compute_xg_ratings is not None:
        try:
            xr = compute_xg_ratings(ref).get(league)
        except Exception:
            pass
    model = DixonColes()
    if not model.fit(lg_hist, ref, xg_ratings=xr):
        return {"error": f"داده کافی برای لیگ {league} نیست."}

    # injury adjustment (same as the daily pipeline)
    injuries = None
    try:
        from predict_today import _load_injuries, _match_injuries
        injuries = _match_injuries(_load_injuries(), home_c, away_c) or None
    except Exception:
        injuries = None

    menu = model.all_markets(home_c, away_c, min_prob=0.02, injuries=injuries)
    src_idx = _sources_for(home_c, away_c)

    # real 1X2 odds from the fixture (football-data), if present
    real_odds = {}
    if fixture is not None:
        mkt = (devig_power(fixture.get("odds_h"), fixture.get("odds_d"), fixture.get("odds_a"))
               or devig_1x2(fixture.get("odds_h"), fixture.get("odds_d"), fixture.get("odds_a")))
        for key, o in (("1", fixture.get("max_h") or fixture.get("odds_h")),
                       ("X", fixture.get("max_d") or fixture.get("odds_d")),
                       ("2", fixture.get("max_a") or fixture.get("odds_a"))):
            if o and float(o) > 1:
                real_odds[("1X2", key)] = float(o)
        for key, o in (("Over", fixture.get("odds_over25")), ("Under", fixture.get("odds_under25"))):
            try:
                if o and float(o) > 1:
                    real_odds[("OU25", key)] = float(o)
            except (TypeError, ValueError):
                pass

    # attach sources, real odds, EV, and min-odds filter
    enriched = []
    for o in menu["options"]:
        if o["fair_odds"] < min_odds:
            continue
        key = (o["market"], o["pick"])
        srcs = src_idx.get(key, {})
        ro = real_odds.get(key)
        ev = round(o["p"] * ro - 1, 3) if ro else None
        enriched.append({**o,
                         "real_odds": ro,
                         "ev": ev,
                         "value": (ev is not None and ev >= 0.05),
                         "n_sources": len(srcs),
                         "sources": sorted(srcs.keys())})
    result = {
        "match": f"{home_c} — {away_c}", "league": league,
        "date": str(fixture["date"].date()) if fixture is not None else None,
        "time": str(fixture.get("time") or "") if fixture is not None else "",
        "xg": f"{menu['xg_home']}-{menu['xg_away']}",
        "known_teams": menu["known_teams"],
        "injuries": injuries or {},
        "min_odds": min_odds,
        "n_options": len(enriched),
        "options": enriched,
    }
    with open(os.path.join(OUT, "match_analysis.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def _list_today():
    if load_all_fixtures is None:
        print("fixtures loader unavailable"); return
    fx = load_all_fixtures(days_ahead=2)
    for _, r in fx.iterrows():
        print(f"  [{r['league']}] {r['home']} vs {r['away']}  {r.get('time') or ''}")


def _print_fa(res):
    if res.get("error"):
        print("⚠️", res["error"]); return
    print(f"⚽ تحلیل کامل: {res['match']}  ({res['league']}{'، '+res['time'] if res['time'] else ''})")
    print(f"xG مدل: {res['xg']}" + ("" if res["known_teams"] else "  ⚠️ داده یک تیم کم است"))
    if res.get("injuries"):
        ih, ia = res["injuries"].get("home", 0), res["injuries"].get("away", 0)
        print(f"🚑 مصدوم/محروم اعمال‌شده در مدل: میزبان {ih or 0} — مهمان {ia or 0}")
    if res["min_odds"] > 1:
        print(f"فیلتر: فقط آپشن‌های با ضریب منصفانه ≥ {res['min_odds']}")
    print(f"\n{res['n_options']} آپشن (مرتب بر اساس احتمال):\n")
    for o in res["options"]:
        line = f"• {o['label_fa']}: احتمال {round(o['p']*100)}٪ | ضریب منصفانه {o['fair_odds']}"
        if o.get("real_odds"):
            line += f" | ضریب بازار {o['real_odds']}"
            if o.get("ev") is not None:
                line += f" (ارزش {'+' if o['ev']>=0 else ''}{round(o['ev']*100)}٪{' 💎' if o['value'] else ''})"
        if o["n_sources"]:
            line += f" | 🤝 {o['n_sources']} منبع: {'، '.join(o['sources'][:4])}"
        print(line)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--list" in args:
        _list_today(); raise SystemExit(0)
    min_odds = 1.0
    if "--min-odds" in args:
        k = args.index("--min-odds")
        min_odds = float(args[k + 1]); del args[k:k + 2]
    if len(args) < 2:
        print('usage: python3 analyze_match.py "Home Team" "Away Team" [--min-odds 1.8]'); raise SystemExit(1)
    res = analyze(args[0], args[1], min_odds=min_odds)
    _print_fa(res)
