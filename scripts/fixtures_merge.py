# -*- coding: utf-8 -*-
"""Build the unified fixture list: football-data.co.uk (odds) + FotMob (coverage).

football-data fixtures carry market odds but cover ~13 leagues. FotMob covers
40+ leagues but no odds. We take football-data as the odds-bearing base, then
append FotMob-only matches (fuzzy-deduped) so the model can still predict them
(odds-free -> model-only probabilities, no de-vig).
"""
import os

import pandas as pd
from curl_cffi import requests as cr

from data_loader import load_fixtures
from merge_rank import team_sim

# (FotMob country code, keyword in league name) -> our history league name.
# Keyed this way because FotMob names collide ("Serie A" = Brazil AND Italy)
# and vary ("Liga Profesional Clausura"). We only map leagues our model covers.
FOTMOB_MAP = [
    ("BRA", "Serie A", "Brazil Serie A"),
    ("ARG", "Liga Profesional", "Argentina Liga Profesional"),
    ("SWE", "Allsvenskan", "Sweden Allsvenskan"),
    ("NOR", "Eliteserien", "Norway Eliteserien"),
    ("FIN", "Veikkausliiga", "Finland Veikkausliiga"),
    ("DEN", "Superligaen", "Denmark Superliga"),
    ("USA", "MLS", "USA MLS"),
    ("MEX", "Liga MX", "Mexico Liga MX"),
    ("JPN", "J1", "Japan J1 League"),
    ("CHN", "Super League", "China Super League"),
    ("POL", "Ekstraklasa", "Poland Ekstraklasa"),
    ("ROU", "Superliga", "Romania Superliga"),
    ("RUS", "Premier League", "Russia Premier League"),
    ("ENG", "Premier League", "England Premier League"),
    ("ESP", "LaLiga", "Spain La Liga"),
    ("GER", "Bundesliga", "Germany Bundesliga"),
    ("ITA", "Serie A", "Italy Serie A"),
    ("FRA", "Ligue 1", "France Ligue 1"),
    ("NED", "Eredivisie", "Netherlands Eredivisie"),
    ("SCO", "Premiership", "Scotland Premiership"),
    ("TUR", "Süper Lig", "Turkey Super Lig"),
    ("GRE", "Super League", "Greece Super League"),
    ("POR", "Liga Portugal", "Portugal Liga"),
    ("BEL", "Pro League", "Belgium Pro League"),
]
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.fotmob.com/"}


def _map_league(ccode, name):
    for c, kw, out in FOTMOB_MAP:
        if ccode == c and kw.lower() in (name or "").lower():
            return out
    return None


def _same_match(ah, aa, bh, ba):
    return min(team_sim(ah, bh), team_sim(aa, ba)) >= 80


def _fotmob_day(day):
    """Return list of dicts for one date, with our league names, exclude finished."""
    url = f"https://www.fotmob.com/api/data/matches?date={day.strftime('%Y%m%d')}"
    out = []
    try:
        r = cr.get(url, headers=_HEADERS, timeout=20, impersonate="chrome110")
        if r.status_code != 200:
            return out
        data = r.json()
    except Exception:
        return out
    for lb in data.get("leagues", []):
        league = _map_league(lb.get("ccode"), lb.get("name"))
        if not league:
            continue
        for m in lb.get("matches", []):
            st = m.get("status", {})
            if st.get("finished") or st.get("started"):
                continue
            t = st.get("utcTime", "")
            out.append({"league": league, "date": pd.Timestamp(day),
                        "time": t[11:16] if len(t) >= 16 else "",
                        "home": m["home"]["name"], "away": m["away"]["name"],
                        "odds_h": None, "odds_d": None, "odds_a": None,
                        "max_h": None, "max_d": None, "max_a": None,
                        "odds_over25": None, "odds_under25": None, "src": "fotmob"})
    return out


def load_all_fixtures(days_ahead=2):
    ref = pd.Timestamp.now(tz="Asia/Tehran").tz_localize(None).normalize()
    base = load_fixtures()
    base = base[(base["date"] >= ref) & (base["date"] < ref + pd.Timedelta(days=days_ahead))].copy()
    base["src"] = "football-data"

    rows = []
    for d_off in range(days_ahead):
        rows.extend(_fotmob_day((ref + pd.Timedelta(days=d_off)).date()))
    fot = pd.DataFrame(rows)
    if fot.empty:
        return base.reset_index(drop=True)

    extra = []
    for _, f in fot.iterrows():
        dup = base[(base["league"] == f["league"]) & (base["date"] == f["date"])]
        if any(_same_match(f["home"], f["away"], b.home, b.away) for b in dup.itertuples()):
            continue
        extra.append(f)
    if extra:
        base = pd.concat([base, pd.DataFrame(extra)], ignore_index=True)
    return base.sort_values(["date", "time"]).reset_index(drop=True)


if __name__ == "__main__":
    fx = load_all_fixtures()
    print(f"total fixtures: {len(fx)}")
    print(fx.groupby("src").size())
    print(fx.groupby("league").size().sort_values(ascending=False))
