# -*- coding: utf-8 -*-
"""Unified fixture list: football-data.co.uk (odds base) + global live coverage.

football-data fixtures carry market odds but cover ~13 leagues. The live layer
(scripts/live_fixtures.py) adds worldwide coverage from a strict-priority
waterfall of sources (Sports Mole -> AiScore -> Sofascore -> ESPN -> 365scores
-> Livescore -> Flashscore -> FotMob -> prexzy). Live matches carry no odds, so
we take football-data as the odds-bearing base and append live-only matches
(fuzzy-deduped) — the model still prices them (model-only probs, no de-vig).

The live layer also writes the comprehensive worldwide list to
output/all_fixtures.json (+ .md) as a side effect of load_live_fixtures().
"""
import os
import sys

import pandas as pd

from data_loader import load_fixtures
from merge_rank import team_sim

try:
    from live_fixtures import load_live_fixtures
except Exception as _ex:  # never let a live-layer import error kill predictions
    def load_live_fixtures(days_ahead=2):
        sys.stderr.write(f"[fixtures_merge] live layer unavailable: {_ex}\n")
        return []

_COLS = ["league", "date", "time", "home", "away", "odds_h", "odds_d", "odds_a",
         "max_h", "max_d", "max_a", "odds_over25", "odds_under25"]


def _same_match(ah, aa, bh, ba):
    return min(team_sim(ah, bh), team_sim(aa, ba)) >= 80


def load_all_fixtures(days_ahead=2):
    ref = pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize()

    base = load_fixtures()
    if base is None or base.empty:
        base = pd.DataFrame(columns=_COLS)
    base = base[(base["date"] >= ref) & (base["date"] < ref + pd.Timedelta(days=days_ahead))].copy()
    base["src"] = "football-data"
    base = base.reset_index(drop=True)

    try:
        live_rows = load_live_fixtures(days_ahead=days_ahead) or []
    except Exception as ex:
        sys.stderr.write(f"[fixtures_merge] live layer error: {type(ex).__name__}: {ex}\n")
        live_rows = []
    live = pd.DataFrame(live_rows)

    if not live.empty:
        extra = []
        for _, f in live.iterrows():
            dup = base[(base["league"] == f["league"]) & (base["date"] == f["date"])]
            matched = False
            for b in dup.itertuples():
                if _same_match(f["home"], f["away"], b.home, b.away):
                    matched = True
                    # The football-data row keeps its odds; just backfill a
                    # kickoff time onto it if the odds file didn't carry one.
                    if not str(getattr(b, "time", "") or "").strip() and str(f.get("time") or "").strip():
                        base.loc[b.Index, "time"] = f["time"]
                    break
            if not matched:
                extra.append(f)
        if extra:
            base = pd.concat([base, pd.DataFrame(extra)], ignore_index=True)

    if "time" in base.columns:
        base["time"] = base["time"].fillna("")  # avoid float-NaN vs str sort error
    return base.sort_values(["date", "time"]).reset_index(drop=True)


if __name__ == "__main__":
    fx = load_all_fixtures()
    print(f"total fixtures: {len(fx)}")
    print(fx.groupby("src").size())
    print(fx.groupby("league").size().sort_values(ascending=False))
