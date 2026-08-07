"""Honest walk-forward backtest of the Dixon-Coles engine.

For every league with enough matches in the test window, refit the model every
21 days using ONLY data before the refit date (no leakage), predict the next
21 days, then score against reality and against the de-vigged bookmaker
baseline. Also simulates a flat-stake value strategy and tunes the
model/market blend weight.

Usage:
    python3 backtest.py                 # rolling window over the last 180 days
    python3 backtest.py --rolling 90    # last 90 days instead
    python3 backtest.py --start 2026-01-01 --end 2026-07-24   # fixed window

The window is ROLLING by default: it always ends at the newest match in the
history feed, so re-running it each week re-scores the freshest period and
exposes model drift (see the monthly breakdown in the results).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from data_loader import load_history
from dixon_coles import DixonColes, devig_1x2

ROLLING_DAYS = 180
REFIT_DAYS = 21
MIN_TEST_MATCHES = 40
EV_THRESHOLD = 0.05
BLEND_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


def _parse_window(argv):
    """Return (start, end). Default: rolling window ending at the latest data."""
    start = end = None
    if "--start" in argv:
        start = pd.Timestamp(argv[argv.index("--start") + 1])
    if "--end" in argv:
        end = pd.Timestamp(argv[argv.index("--end") + 1])
    if "--rolling" in argv:
        days = int(argv[argv.index("--rolling") + 1])
    else:
        days = ROLLING_DAYS
    return start, end, days


def run():
    hist = load_history()
    start_arg, end_arg, roll_days = _parse_window(sys.argv[1:])
    TEST_END = end_arg or (hist["date"].max().normalize() + pd.Timedelta(days=1))
    TEST_START = start_arg or (TEST_END - pd.Timedelta(days=roll_days))
    print(f"backtest window: {TEST_START.date()} .. {TEST_END.date()} "
          f"({'fixed' if start_arg else f'rolling {roll_days}d'})")
    rows = []
    leagues = sorted(hist["league"].unique())
    for lg in leagues:
        d = hist[hist["league"] == lg]
        test = d[(d["date"] >= TEST_START) & (d["date"] < TEST_END)]
        if len(test) < MIN_TEST_MATCHES:
            continue
        period = TEST_START
        while period < TEST_END:
            pend = period + pd.Timedelta(days=REFIT_DAYS)
            chunk = test[(test["date"] >= period) & (test["date"] < pend)]
            if len(chunk) == 0:
                period = pend
                continue
            model = DixonColes()
            if not model.fit(d, period):
                period = pend
                continue
            for _, r in chunk.iterrows():
                p = model.predict(r["home"], r["away"])
                mkt = devig_1x2(r["odds_h"], r["odds_d"], r["odds_a"])
                outcome = 0 if r["hg"] > r["ag"] else (1 if r["hg"] == r["ag"] else 2)
                rows.append({
                    "league": lg,
                    "date": str(r["date"].date()),
                    "known": p["known_teams"],
                    "ph": p["p_home"], "pd": p["p_draw"], "pa": p["p_away"],
                    "po25": p["p_over25"],
                    "mh": mkt[0] if mkt is not None else np.nan,
                    "md": mkt[1] if mkt is not None else np.nan,
                    "ma": mkt[2] if mkt is not None else np.nan,
                    "oh": r["odds_h"], "od": r["odds_d"], "oa": r["odds_a"],
                    "outcome": outcome,
                    "over25": int(r["hg"] + r["ag"] >= 3),
                })
            period = pend
        sys.stderr.write(f"done {lg}\n")

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(os.path.dirname(__file__), "..", "output", "backtest_matches.csv"), index=False)

    # ---------------------------------------------------------------- scoring
    res = {"n_total": len(df), "n_known": int(df["known"].sum()),
           "window": f"{TEST_START.date()} .. {TEST_END.date()}", "leagues": int(df["league"].nunique())}

    probs = df[["ph", "pd", "pa"]].values
    onehot = np.eye(3)[df["outcome"].values]
    res["model_pick_accuracy"] = float((probs.argmax(1) == df["outcome"].values).mean())
    res["model_brier"] = float(((probs - onehot) ** 2).sum(1).mean())
    res["model_logloss"] = float(-np.log(np.clip(probs[np.arange(len(df)), df["outcome"]], 1e-12, 1)).mean())

    hasm = df.dropna(subset=["mh"])
    if len(hasm):
        mp = hasm[["mh", "md", "ma"]].values
        oh1 = np.eye(3)[hasm["outcome"].values]
        res["n_with_odds"] = len(hasm)
        res["market_pick_accuracy"] = float((mp.argmax(1) == hasm["outcome"].values).mean())
        res["market_brier"] = float(((mp - oh1) ** 2).sum(1).mean())
        res["market_logloss"] = float(-np.log(np.clip(mp[np.arange(len(hasm)), hasm["outcome"]], 1e-12, 1)).mean())
        # blend tuning
        bp = hasm[["ph", "pd", "pa"]].values
        blends = {}
        for w in BLEND_GRID:
            bl = w * bp + (1 - w) * mp
            blends[str(w)] = float(((bl - oh1) ** 2).sum(1).mean())
        res["blend_brier_by_weight"] = blends
        res["best_blend_weight"] = float(min(blends, key=blends.get))
        # operating weight: keep a little model signal even when market wins
        w_op = res["best_blend_weight"] if res["best_blend_weight"] > 0 else 0.25
        res["operating_blend_weight"] = w_op

        # accuracy at high confidence (what the daily report's top picks look like)
        bl = w_op * bp + (1 - w_op) * mp
        top_p = bl.max(1)
        top_k = bl.argmax(1)
        hit = (top_k == hasm["outcome"].values)
        for thr in (0.55, 0.60, 0.65, 0.70):
            m = top_p >= thr
            if m.sum() >= 30:
                res[f"acc_at_conf_{int(thr*100)}"] = {
                    "n": int(m.sum()), "accuracy": round(float(hit[m].mean()), 3),
                    "avg_predicted": round(float(top_p[m].mean()), 3)}

        # flat-stake value sim on best available odds, 1X2, EV >= threshold
        pnl, nbets, wins = 0.0, 0, 0
        w = w_op
        for _, r in hasm.iterrows():
            pb = [w * r["ph"] + (1 - w) * r["mh"], w * r["pd"] + (1 - w) * r["md"], w * r["pa"] + (1 - w) * r["ma"]]
            odds = [r["oh"], r["od"], r["oa"]]
            evs = [pb[k] * odds[k] - 1 for k in range(3)]
            k = int(np.argmax(evs))
            if evs[k] >= EV_THRESHOLD and pb[k] >= 0.25:
                nbets += 1
                if r["outcome"] == k:
                    pnl += odds[k] - 1
                    wins += 1
                else:
                    pnl -= 1
        res["value_sim"] = {"bets": nbets, "wins": wins, "pnl_units": round(pnl, 1),
                            "roi": round(pnl / nbets, 4) if nbets else None,
                            "ev_threshold": EV_THRESHOLD}

    # over 2.5 accuracy (model side pick)
    res["ou25_pick_accuracy"] = float(((df["po25"].values >= 0.5).astype(int) == df["over25"].values).mean())

    # calibration on home-win probability
    bins = np.linspace(0, 1, 11)
    cal = []
    bidx = np.digitize(df["ph"].values, bins) - 1
    for b in range(10):
        m = bidx == b
        if m.sum() >= 25:
            cal.append({"bin": f"{bins[b]:.1f}-{bins[b+1]:.1f}",
                        "n": int(m.sum()),
                        "predicted": round(float(df['ph'].values[m].mean()), 3),
                        "actual": round(float((df['outcome'].values[m] == 0).mean()), 3)})
    res["calibration_home_win"] = cal

    # per-league quick table
    per = []
    for lg, g in df.groupby("league"):
        gp = g[["ph", "pd", "pa"]].values
        per.append({"league": lg, "n": len(g),
                    "acc": round(float((gp.argmax(1) == g["outcome"].values).mean()), 3)})
    res["per_league"] = sorted(per, key=lambda x: -x["n"])

    # monthly drift check: accuracy + log-loss per calendar month so we can see
    # whether the model is degrading or improving over the rolling window
    monthly = []
    df = df.assign(month=pd.to_datetime(df["date"]).dt.to_period("M").astype(str))
    for mo, g in df.groupby("month"):
        gp = g[["ph", "pd", "pa"]].values
        oh1 = np.eye(3)[g["outcome"].values]
        ll = float(-np.log(np.clip(gp[np.arange(len(g)), g["outcome"]], 1e-12, 1)).mean())
        monthly.append({"month": mo, "n": len(g),
                        "acc": round(float((gp.argmax(1) == g["outcome"].values).mean()), 3),
                        "logloss": round(ll, 4)})
    res["monthly"] = monthly

    out = os.path.join(os.path.dirname(__file__), "..", "output", "backtest_results.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: v for k, v in res.items() if k not in ("per_league", "calibration_home_win")}, indent=2))


if __name__ == "__main__":
    run()
