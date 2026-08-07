"""Fit per-league models and predict all upcoming fixtures (next 2 days).

Output: output/model_predictions.json — one record per fixture with model
probabilities for every market, de-vigged market probabilities, blended
probabilities, and EV per 1X2 side at best (Max) available odds.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from data_loader import load_history
from dixon_coles import DixonColes, devig_1x2, devig_power

try:
    from fixtures_merge import load_all_fixtures
except Exception:
    from data_loader import load_fixtures as _lf
    def load_all_fixtures(days_ahead=2):
        import pandas as _pd
        ref = _pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize()
        fx = _lf()
        return fx[(fx["date"] >= ref) & (fx["date"] < ref + _pd.Timedelta(days=days_ahead))]

try:
    from xg_ratings import compute_xg_ratings
except Exception:
    compute_xg_ratings = None

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
BLEND_W = 0.5  # model weight vs market; overridden by backtest result if present


def _load_injuries():
    p = os.path.join(OUT, "injuries.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return []


def _match_injuries(injuries, home, away):
    """Rough count of injured players per side by fuzzy team-name containment."""
    if not injuries:
        return {}
    from merge_rank import team_sim
    out = {}
    for side, name in (("home", home), ("away", away)):
        cnt = sum(1 for inj in injuries if team_sim(name, inj.get("team", "")) >= 85)
        if cnt:
            out[side] = cnt
    return out


def blend_weight():
    p = os.path.join(OUT, "backtest_results.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                d = json.load(f)
            return float(d.get("operating_blend_weight") or d.get("best_blend_weight") or BLEND_W)
        except Exception:
            pass
    return BLEND_W


def _save_predictions(records, w):
    """همیشه model_predictions.json را می‌نویسد — حتی اگر خالی باشد.

    مرحله‌های بعدی (merge_rank/report_gen/webapp_data) این فایل را با open()
    بدون گارد می‌خوانند؛ اگر نوشته نشود کل پایپ‌لاین با FileNotFoundError آبشاری
    می‌ترکد و سایت آپدیت نمی‌شود. نبودِ بازی یا خطای یک بازی نباید کل گزارش را بکشد.
    """
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "model_predictions.json"), "w") as f:
        json.dump({"generated_at": str(pd.Timestamp.now()), "blend_weight": w,
                   "n_fixtures": len(records), "fixtures": records}, f,
                  indent=1, ensure_ascii=False)


def run(days_ahead=2):
    ref = pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize()
    hist = load_history()
    fx = load_all_fixtures(days_ahead=days_ahead)
    if fx.empty:
        print("no fixtures in window")
        _save_predictions([], blend_weight())
        return

    # injuries lookup (by league_code -> team -> count); optional
    injuries = _load_injuries()

    # point-in-time xG ratings for the six Understat leagues
    xg_all = {}
    if compute_xg_ratings is not None:
        try:
            xg_all = compute_xg_ratings(ref + pd.Timedelta(days=1))
        except Exception as ex:
            sys.stderr.write(f"xG ratings unavailable: {ex}\n")

    w = blend_weight()
    models = {}
    records = []
    for lg in fx["league"].unique():
        try:
            m = DixonColes()
            if m.fit(hist[hist["league"] == lg], ref + pd.Timedelta(days=1),
                     xg_ratings=xg_all.get(lg)):
                models[lg] = m
                nxg = len(getattr(m, "xg_covered", []))
                sys.stderr.write(f"fitted {lg} ({len(m.teams)} teams, {nxg} with xG)\n")
            else:
                sys.stderr.write(f"SKIP {lg} (not enough data)\n")
        except Exception as ex:
            sys.stderr.write(f"SKIP {lg} (fit error: {type(ex).__name__}: {ex})\n")

    for _, r in fx.iterrows():
        m = models.get(r["league"])
        if m is None:
            continue
        try:
            inj = _match_injuries(injuries, r["home"], r["away"])
            p = m.predict(r["home"], r["away"], injuries=inj)
            mkt = devig_power(r["odds_h"], r["odds_d"], r["odds_a"]) or devig_1x2(r["odds_h"], r["odds_d"], r["odds_a"])
            rec = {
                "league": r["league"],
                "date": str(r["date"].date()),
                "time": str(r.get("time") or ""),
                "home": r["home"], "away": r["away"],
                "model": {k: (round(v, 4) if isinstance(v, float) else v)
                          for k, v in p.items() if k not in ("home", "away")},
                "odds": {"h": r["odds_h"], "d": r["odds_d"], "a": r["odds_a"],
                         "max_h": r.get("max_h"), "max_d": r.get("max_d"), "max_a": r.get("max_a"),
                         "over25": r.get("odds_over25"), "under25": r.get("odds_under25")},
                "src": r.get("src", "football-data"),
                "injuries": inj,
            }
            if mkt is not None:
                pb = {
                    "p_home": w * p["p_home"] + (1 - w) * mkt[0],
                    "p_draw": w * p["p_draw"] + (1 - w) * mkt[1],
                    "p_away": w * p["p_away"] + (1 - w) * mkt[2],
                }
                rec["market_devig"] = {"h": round(float(mkt[0]), 4), "d": round(float(mkt[1]), 4), "a": round(float(mkt[2]), 4)}
                rec["blend"] = {k: round(v, 4) for k, v in pb.items()}
                best = [r.get("max_h") or r["odds_h"], r.get("max_d") or r["odds_d"], r.get("max_a") or r["odds_a"]]
                evs = {}
                for k, key in enumerate(["h", "d", "a"]):
                    o = best[k]
                    if o and o > 1:
                        evs[key] = round(float([pb["p_home"], pb["p_draw"], pb["p_away"]][k] * o - 1), 4)
                rec["ev_1x2"] = evs
            rec = json.loads(json.dumps(rec, default=lambda x: None if (isinstance(x, float) and np.isnan(x)) else x))
            records.append(rec)
        except Exception as ex:
            # یک بازیِ خراب نباید کل پیش‌بینی و در نتیجه کل سایت را از کار بیندازد.
            sys.stderr.write(f"SKIP fixture {r.get('home')} vs {r.get('away')} "
                             f"({r.get('league')}): {type(ex).__name__}: {ex}\n")
            continue

    _save_predictions(records, w)
    print(f"predicted {len(records)} fixtures across {len(models)} leagues (blend w={w})")


if __name__ == "__main__":
    run()
