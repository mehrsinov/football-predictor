# -*- coding: utf-8 -*-
"""Focused A/B backtest: does the xG blend improve the model?

Walk-forward over the six Understat leagues, refit every 21 days, and for each
match score TWO models on the same fixtures:
    A) goals-only Dixon-Coles         (baseline)
    B) goals + xG blend               (candidate)
Compare Brier score and pick accuracy. Strictly point-in-time xG.
"""
import json
import os

import numpy as np
import pandas as pd

from data_loader import load_history
from dixon_coles import DixonColes
from xg_ratings import compute_xg_ratings, XG_LEAGUE_MAP

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
LEAGUES = list(XG_LEAGUE_MAP.values())
START = pd.Timestamp("2026-01-01")
END = pd.Timestamp("2026-06-01")   # big-5 seasons run to late May
STEP = 21


def brier(probs, outcomes):
    oh = np.eye(3)[outcomes]
    return float(((probs - oh) ** 2).sum(1).mean())


def run():
    hist = load_history()
    A_probs, B_probs, outs = [], [], []
    for lg in LEAGUES:
        d = hist[hist["league"] == lg]
        test = d[(d["date"] >= START) & (d["date"] < END)]
        if len(test) < 30:
            continue
        period = START
        while period < END:
            pend = period + pd.Timedelta(days=STEP)
            chunk = test[(test["date"] >= period) & (test["date"] < pend)]
            if len(chunk):
                xr = compute_xg_ratings(period).get(lg)
                mA = DixonColes()
                okA = mA.fit(d, period)
                mB = DixonColes()
                okB = mB.fit(d, period, xg_ratings=xr)
                if okA and okB:
                    for _, r in chunk.iterrows():
                        pa = mA.predict(r["home"], r["away"])
                        pb = mB.predict(r["home"], r["away"])
                        A_probs.append([pa["p_home"], pa["p_draw"], pa["p_away"]])
                        B_probs.append([pb["p_home"], pb["p_draw"], pb["p_away"]])
                        outs.append(0 if r["hg"] > r["ag"] else (1 if r["hg"] == r["ag"] else 2))
            period = pend

    A = np.array(A_probs); B = np.array(B_probs); o = np.array(outs)
    res = {
        "n": len(o), "leagues": LEAGUES,
        "baseline_brier": round(brier(A, o), 4),
        "xg_brier": round(brier(B, o), 4),
        "baseline_acc": round(float((A.argmax(1) == o).mean()), 4),
        "xg_acc": round(float((B.argmax(1) == o).mean()), 4),
    }
    res["brier_improvement_pct"] = round(100 * (res["baseline_brier"] - res["xg_brier"]) / res["baseline_brier"], 2)
    res["verdict"] = "xG helps" if res["xg_brier"] < res["baseline_brier"] else "xG neutral/worse"
    with open(os.path.join(OUT, "xg_backtest.json"), "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run()
