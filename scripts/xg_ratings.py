# -*- coding: utf-8 -*-
"""Point-in-time xG-based attack/defence adjustments from Understat data.

xG is more predictive of future goals than past goals (well established), so we
nudge the Dixon-Coles ratings (fit on real goals) toward each team's underlying
xG performance. Everything is strictly point-in-time (only xG from BEFORE the
as-of date is used), so this is backtest-safe.

Returns, per Understat league, a dict:  team -> {"xatk": float, "xdef": float}
on the same log scale as Dixon-Coles ratings (0 = league average; +atk = scores
more; -def = concedes less), so they can be blended directly.
"""
import glob
import os

import numpy as np
import pandas as pd

PROC = os.path.join(os.path.dirname(__file__), "..", "data", "processed")

# Understat league_code -> our history league name (must match data_loader)
XG_LEAGUE_MAP = {
    "EPL": "England Premier League", "La_liga": "Spain La Liga",
    "Bundesliga": "Germany Bundesliga", "Serie_A": "Italy Serie A",
    "Ligue_1": "France Ligue 1", "RFPL": "Russia Premier League",
}

# Understat name -> football-data name (extend as needed; big clubs covered)
TEAM_ALIASES = {
    "Manchester United": "Man United", "Manchester City": "Man City",
    "Newcastle United": "Newcastle", "Wolverhampton Wanderers": "Wolves",
    "Tottenham": "Tottenham", "West Bromwich Albion": "West Brom",
    "Sheffield United": "Sheffield United", "Nottingham Forest": "Nott'm Forest",
    "Paris Saint Germain": "Paris SG", "RasenBallsport Leipzig": "RB Leipzig",
    "Bayer Leverkusen": "Leverkusen", "Borussia M.Gladbach": "M'gladbach",
    "Borussia Dortmund": "Dortmund", "Eintracht Frankfurt": "Ein Frankfurt",
    "Atletico Madrid": "Ath Madrid", "Athletic Club": "Ath Bilbao",
    "Real Sociedad": "Sociedad", "Celta Vigo": "Celta", "Real Betis": "Betis",
    "Deportivo La Coruna": "La Coruna", "Real Valladolid": "Valladolid",
    "Internazionale": "Inter", "AC Milan": "Milan", "Hellas Verona": "Verona",
    "Dynamo Moscow": "Dynamo Moscow", "Dinamo Moscow": "Dynamo Moscow",
    "Spartak Moscow": "Spartak Moscow", "CSKA Moscow": "CSKA Moscow",
    "Zenit St. Petersburg": "Zenit", "Nizhny Novgorod": "Pari NN",
    "Paris FC": "Paris FC", "Saint-Etienne": "St Etienne",
}


def _canon(name):
    return TEAM_ALIASES.get(name, name)


def compute_xg_ratings(as_of, half_life_days=240.0, lookback_days=550):
    """Return {league_name: {team: {"xatk","xdef"}}} using xG before as_of."""
    as_of = pd.Timestamp(as_of)
    out = {}
    for path in glob.glob(os.path.join(PROC, "understat_*.csv")):
        code = os.path.basename(path)[len("understat_"):-4]
        league = XG_LEAGUE_MAP.get(code)
        if not league:
            continue
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df = df[(df["date"] < as_of) & (df["date"] >= as_of - pd.Timedelta(days=lookback_days))]
        if len(df) < 40:
            continue
        w = np.exp(-np.log(2) / half_life_days * (as_of - df["date"]).dt.days.values)
        lg_xg = np.average((df["xg_h"].values + df["xg_a"].values) / 2, weights=w)
        if lg_xg <= 0:
            continue
        # accumulate weighted xG for/against per team
        stat = {}
        for (h, a, xh, xa, wt) in zip(df["home"], df["away"], df["xg_h"], df["xg_a"], w):
            for team, gf, ga in ((h, xh, xa), (a, xa, xh)):
                s = stat.setdefault(_canon(team), [0.0, 0.0, 0.0])
                s[0] += wt * gf
                s[1] += wt * ga
                s[2] += wt
        ratings = {}
        for team, (gf, ga, tw) in stat.items():
            if tw < 3:
                continue
            atk = np.log(max(gf / tw, 0.05) / lg_xg)   # >0 => scores above avg
            dfn = -np.log(max(ga / tw, 0.05) / lg_xg)  # >0 => concedes below avg
            ratings[team] = {"xatk": float(np.clip(atk, -1.5, 1.5)),
                             "xdef": float(np.clip(dfn, -1.5, 1.5)), "n": float(tw)}
        if ratings:
            out[league] = ratings
    return out


if __name__ == "__main__":
    r = compute_xg_ratings(pd.Timestamp.now().normalize())
    for lg, teams in r.items():
        top = sorted(teams.items(), key=lambda kv: kv[1]["xatk"], reverse=True)[:3]
        print(f"{lg}: {len(teams)} teams | top xG attack: {[(t, round(v['xatk'],2)) for t,v in top]}")
