# -*- coding: utf-8 -*-
"""Assemble the rich dataset that powers the web app (docs/index.html).

Exports one JSON blob with: today's fixtures (+probabilities, scoreline matrix,
full market menu with sources), per-team profiles (ratings, last-5 form, avg
shots/corners/cards where available, xG rating), league params for client-side
match simulation, computed standings, H2H, injuries, and backtest transparency.
"""
import json
import os

import numpy as np
import pandas as pd

from data_loader import load_history
from dixon_coles import DixonColes
from merge_rank import team_sim

try:
    from xg_ratings import compute_xg_ratings
except Exception:
    compute_xg_ratings = None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
RAW = os.path.join(HERE, "..", "data", "raw")


def _load(name, default):
    p = os.path.join(OUT, name)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _form5(hist, league, team, ref):
    g = hist[(hist["league"] == league) & (hist["date"] < ref) &
             ((hist["home"] == team) | (hist["away"] == team))].tail(5)
    out = []
    for _, r in g.iterrows():
        is_home = r["home"] == team
        gf, ga = (r["hg"], r["ag"]) if is_home else (r["ag"], r["hg"])
        res = "W" if gf > ga else ("D" if gf == ga else "L")
        out.append({"res": res, "score": f"{int(r['hg'])}-{int(r['ag'])}",
                    "vs": r["away"] if is_home else r["home"],
                    "home": bool(is_home), "date": str(r["date"].date())})
    return out


def _team_match_stats(league, team):
    """Avg shots/shots-on-target/corners/cards from main-league CSVs (last 20)."""
    import glob as _glob
    frames = []
    for p in _glob.glob(os.path.join(RAW, "main_*.csv")):
        try:
            df = pd.read_csv(p, encoding="utf-8-sig", on_bad_lines="skip",
                             usecols=lambda c: c in {"Date", "HomeTeam", "AwayTeam", "HS", "AS",
                                                     "HST", "AST", "HC", "AC", "HY", "AY", "HR", "AR"})
        except Exception:
            continue
        if "HomeTeam" not in df.columns or "HS" not in df.columns:
            continue
        m = df[(df["HomeTeam"] == team) | (df["AwayTeam"] == team)]
        if len(m):
            frames.append(m)
    if not frames:
        return None
    m = pd.concat(frames).tail(20)
    if m.empty:
        return None
    def side_avg(hc, ac):
        vals = np.where(m["HomeTeam"] == team, pd.to_numeric(m[hc], errors="coerce"),
                        pd.to_numeric(m[ac], errors="coerce"))
        vals = vals[~np.isnan(vals)]
        return round(float(vals.mean()), 1) if len(vals) else None
    st = {"shots": side_avg("HS", "AS"), "sot": side_avg("HST", "AST"),
          "corners": side_avg("HC", "AC"), "yellows": side_avg("HY", "AY")}
    return st if any(v is not None for v in st.values()) else None


def _standings(hist, league, ref):
    lg = hist[hist["league"] == league]
    # calendar-year leagues (summer) vs autumn-spring
    start = pd.Timestamp(ref.year, 1, 1)
    if len(lg[lg["date"] >= start]) < 30:  # season likely Aug-May
        start = pd.Timestamp(ref.year - 1, 8, 1)
    g = lg[(lg["date"] >= start) & (lg["date"] < ref)]
    if len(g) < 20:
        return []
    tab = {}
    for _, r in g.iterrows():
        for team, gf, ga in ((r["home"], r["hg"], r["ag"]), (r["away"], r["ag"], r["hg"])):
            t = tab.setdefault(team, {"team": team, "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
            t["p"] += 1; t["gf"] += int(gf); t["ga"] += int(ga)
            if gf > ga: t["w"] += 1; t["pts"] += 3
            elif gf == ga: t["d"] += 1; t["pts"] += 1
            else: t["l"] += 1
    rows = sorted(tab.values(), key=lambda t: (-t["pts"], -(t["gf"] - t["ga"]), -t["gf"]))
    return rows[:24]


def build():
    ref = pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize() + pd.Timedelta(days=1)
    hist = load_history()
    preds = _load("model_predictions.json", {"fixtures": []})
    ranked = _load("ranked_options.json", {"options": [], "tips_only": []})
    bt = _load("backtest_results.json", {})

    xg_all = {}
    if compute_xg_ratings is not None:
        try:
            xg_all = compute_xg_ratings(ref)
        except Exception:
            pass

    # fit models for every league with enough data (powers profiles + matrices + compare)
    leagues = sorted(hist["league"].unique())
    models, league_params = {}, {}
    for lg in leagues:
        m = DixonColes()
        if m.fit(hist[hist["league"] == lg], ref, xg_ratings=xg_all.get(lg)):
            models[lg] = m
            league_params[lg] = {"avg_goals": round(m.avg_goals, 3),
                                 "home_adv": round(m.home_adv, 3),
                                 "rho": round(m.rho, 3)}

    # ---------- team profiles
    teams = {}
    main_league_names = {"England", "Scotland", "Germany", "Italy", "Spain", "France",
                         "Netherlands", "Belgium", "Portugal", "Turkey", "Greece"}
    for lg, m in models.items():
        tl = {}
        is_main = any(lg.startswith(c) for c in main_league_names)
        for t in m.teams:
            if m.team_weight.get(t, 0) < 4:
                continue
            prof = {"atk": round(m.attack[t], 3), "dfn": round(m.defence[t], 3),
                    "w": round(m.team_weight.get(t, 0), 1),
                    "form": _form5(hist, lg, t, ref)}
            xr = (xg_all.get(lg) or {}).get(t)
            if xr:
                prof["xatk"] = round(xr["xatk"], 3); prof["xdef"] = round(xr["xdef"], 3)
            if is_main:
                st = _team_match_stats(lg, t)
                if st:
                    prof["stats"] = st
            tl[t] = prof
        if tl:
            teams[lg] = tl

    # ---------- fixtures with matrix, options, h2h
    opts_by_match = {}
    for o in ranked.get("options", []):
        opts_by_match.setdefault((o["home"], o["away"]), []).append(o)

    fixtures = []
    for i, fx in enumerate(preds.get("fixtures", [])):
        lg = fx["league"]
        m = models.get(lg)
        rec = {"id": i, "league": lg, "date": fx["date"], "time": fx.get("time") or "",
               "home": fx["home"], "away": fx["away"],
               "model": {k: fx["model"].get(k) for k in
                         ("p_home", "p_draw", "p_away", "xg_home", "xg_away", "known_teams",
                          "p_over25", "p_btts_yes", "top_scores")},
               "blend": fx.get("blend"), "market_devig": fx.get("market_devig"),
               "odds": fx.get("odds"), "injuries": fx.get("injuries") or {}, "src": fx.get("src")}
        if m is not None:
            mat, lam, mu, _ = m.score_matrix(fx["home"], fx["away"])
            rec["matrix"] = [[round(float(mat[a, b]), 4) for b in range(7)] for a in range(7)]
            menu = m.all_markets(fx["home"], fx["away"])
            rec["menu"] = [{k: o[k] for k in ("market", "pick", "label_fa", "p", "fair_odds")}
                           for o in menu["options"][:40]]
        # attach harvested option sources
        matched = opts_by_match.get((fx["home"], fx["away"]), [])
        rec["options"] = [{"market": o["market"], "pick": o["pick"], "p": o["blend_p"],
                           "odds": o.get("odds"), "ev": o.get("ev"),
                           "value": o.get("value_flag"), "n_src": o["n_sources"],
                           "sources": o.get("source_names", [])} for o in matched[:14]]
        # H2H (any league, last 5 meetings)
        h2h = hist[(((hist["home"] == fx["home"]) & (hist["away"] == fx["away"])) |
                    ((hist["home"] == fx["away"]) & (hist["away"] == fx["home"]))) &
                   (hist["date"] < ref)].tail(5)
        rec["h2h"] = [{"date": str(r["date"].date()), "home": r["home"], "away": r["away"],
                       "score": f"{int(r['hg'])}-{int(r['ag'])}"} for _, r in h2h.iterrows()]
        fixtures.append(rec)

    # ---------- standings for leagues in play today
    standings = {}
    for lg in sorted({f["league"] for f in fixtures}):
        rows = _standings(hist, lg, ref)
        if rows:
            standings[lg] = rows

    import jdatetime
    now = pd.Timestamp.now(tz="Asia/Tehran")
    jd = jdatetime.datetime.fromgregorian(datetime=now.tz_localize(None).to_pydatetime())
    months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
              "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

    data = {
        "meta": {
            "date_fa": f"{jd.day} {months[jd.month-1]} {jd.year}",
            "date": str(now.date()),
            "generated": now.strftime("%Y-%m-%d %H:%M"),
            "n_fixtures": len(fixtures),
            "n_tips": ranked.get("n_tips_total", 0),
            "n_options": ranked.get("n_options_model", 0),
            "sources_line": ranked.get("sources_used", ""),
            "backtest": {k: bt.get(k) for k in
                         ("n_total", "leagues", "model_pick_accuracy", "market_pick_accuracy",
                          "acc_at_conf_60", "acc_at_conf_65", "acc_at_conf_70", "ou25_pick_accuracy",
                          "window")} if bt else {},
        },
        "league_params": league_params,
        "fixtures": fixtures,
        "teams": teams,
        "standings": standings,
    }
    # scrub NaN/inf -> null so the payload is strict JSON (JS JSON.parse-safe)
    def scrub(x):
        if isinstance(x, dict):
            return {k: scrub(v) for k, v in x.items()}
        if isinstance(x, list):
            return [scrub(v) for v in x]
        if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
            return None
        return x
    data = scrub(data)
    path = os.path.join(OUT, "webapp_data.json")
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    kb = os.path.getsize(path) // 1024
    print(f"webapp_data.json: {kb} KB | fixtures={len(fixtures)} | leagues with teams={len(teams)}")
    return data


if __name__ == "__main__":
    build()
