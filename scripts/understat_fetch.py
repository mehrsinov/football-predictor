"""
understat_fetch.py
------------------
Fetches per-match xG data from Understat league pages via the internal JSON API
and writes per-league CSVs to data/processed/.

Usage:
    python3 understat_fetch.py          # fetch all leagues, all years
    python3 understat_fetch.py EPL      # fetch one league only (for testing)

API endpoint discovered from league.min.js:
    GET https://understat.com/getLeagueData/{league}/{season}
    Requires: X-Requested-With: XMLHttpRequest
    Returns JSON with keys: dates, teams, players
    dates[] contains per-match records:
        id, isResult, h{id,title,short_title}, a{...}, goals{h,a}, xG{h,a}, datetime
"""

import json
import os
import sys
import time
import csv
from datetime import datetime, date
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UNDERSTAT_TO_LEAGUE = {
    "EPL":        "England Premier League",
    "La_liga":    "Spain La Liga",
    "Bundesliga": "Germany Bundesliga",
    "Serie_A":    "Italy Serie A",
    "Ligue_1":    "France Ligue 1",
    "RFPL":       "Russia Premier League",
}

LEAGUES = list(UNDERSTAT_TO_LEAGUE.keys())


def _current_years(n_seasons: int = 4):
    """Return the last *n_seasons* Understat season-start years, most recent first.

    Understat labels a season by its starting calendar year (e.g. 2025 = the
    2025/26 season). European seasons roll over in summer, so from July onward
    the current year is the new season; before July we are still in last year's.
    Previously this was a hardcoded [2023, 2024, 2025, 2026] list that would go
    stale and silently stop fetching newer seasons.
    """
    t = date.today()
    start = t.year if t.month >= 7 else t.year - 1
    return [start - i for i in range(n_seasons)]


YEARS = _current_years()

BASE_URL = "https://understat.com"
API_PATH = "/getLeagueData/{league}/{year}"

SLEEP_BETWEEN = 1.5  # seconds — be polite

# Output directory (relative to this script's location)
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "processed"

# ---------------------------------------------------------------------------
# Team name aliases: Understat name -> football-data.co.uk name
# Key = Understat title, Value = canonical football-data.co.uk name
# ---------------------------------------------------------------------------

TEAM_ALIASES: dict[str, str] = {
    # --- EPL ---
    "Manchester United":        "Man United",
    "Manchester City":          "Man City",
    "Newcastle United":         "Newcastle",
    "Wolverhampton Wanderers":  "Wolves",
    "Nottingham Forest":        "Nott'm Forest",
    "Brighton & Hove Albion":   "Brighton",
    "Tottenham Hotspur":        "Tottenham",
    "West Ham United":          "West Ham",
    "Leeds United":             "Leeds",
    "Luton Town":               "Luton",
    "Sheffield United":         "Sheffield United",
    "Norwich City":             "Norwich",
    "Watford":                  "Watford",
    "Burnley":                  "Burnley",
    "Sheffield Utd":            "Sheffield United",

    # --- La Liga ---
    "Athletic Club":            "Ath Bilbao",
    "Atletico Madrid":          "Ath Madrid",
    "Real Betis":               "Betis",
    "Celta Vigo":               "Celta",
    "Rayo Vallecano":           "Vallecano",
    "Real Sociedad":            "Sociedad",
    "Real Valladolid":          "Valladolid",
    "Espanyol":                 "Espanol",
    "Deportivo Alaves":         "Alaves",
    "Deportivo de La Coruna":   "La Coruna",
    "Levante":                  "Levante",
    "Granada CF":               "Granada",
    "Cadiz CF":                 "Cadiz",
    "Elche CF":                 "Elche",
    "Almeria":                  "Almeria",

    # --- Bundesliga ---
    "RasenBallsport Leipzig":   "RB Leipzig",
    "Borussia M.Gladbach":      "M'gladbach",
    "Eintracht Frankfurt":      "Ein Frankfurt",
    "Bayer Leverkusen":         "Leverkusen",
    "Borussia Dortmund":        "Dortmund",
    "FC Heidenheim":            "Heidenheim",
    "VfB Stuttgart":            "Stuttgart",
    "Mainz 05":                 "Mainz",
    "St. Pauli":                "St Pauli",
    "Hamburger SV":             "Hamburg",
    "Hannover 96":              "Hannover",
    "Fortuna Dusseldorf":       "Fortuna Dusseldorf",
    "Paderborn":                "Paderborn",
    "Hertha Berlin":            "Hertha",
    "Arminia Bielefeld":        "Bielefeld",
    "Greuther Furth":           "Greuther Furth",

    # --- Serie A ---
    "AC Milan":                 "Milan",
    "Hellas Verona":            "Verona",
    "Inter":                    "Inter",
    "Parma Calcio 1913":        "Parma",
    "SPAL":                     "SPAL",
    "Frosinone":                "Frosinone",
    "Ascoli":                   "Ascoli",
    "Spezia":                   "Spezia",
    "Salernitana":              "Salernitana",
    "Cremonese":                "Cremonese",

    # --- Ligue 1 ---
    "Paris Saint Germain":      "Paris SG",
    "Saint-Etienne":            "St Etienne",
    "Olympique Lyon":           "Lyon",
    "Olympique Marseille":      "Marseille",
    "AS Monaco":                "Monaco",
    "SM Caen":                  "Caen",
    "Amiens SC":                "Amiens",
    "Dijon FCO":                "Dijon",
    "Girondins Bordeaux":       "Bordeaux",
    "Stade de Reims":           "Reims",
    "RC Lens":                  "Lens",
    "RC Strasbourg Alsace":     "Strasbourg",
    "Stade Brestois 29":        "Brest",
    "Metz":                     "Metz",
    "Troyes":                   "Troyes",
    "Clermont Foot":            "Clermont",

    # --- RFPL (Russia) ---
    "Dinamo Moscow":            "Dynamo Moscow",
    "FC Krasnodar":             "Krasnodar",
    "FK Akhmat":                "Akhmat Grozny",
    "FC Rostov":                "FK Rostov",
    "FC Orenburg":              "Orenburg",
    "Krylya Sovetov Samara":    "Krylya Sovetov",
    "Zenit St. Petersburg":     "Zenit",
    "Nizhny Novgorod":          "Pari NN",
    "Fakel":                    "Fakel Voronezh",
    "Akron":                    "Akron Togliatti",
    "Lokomotiv":                "Lokomotiv Moscow",
    "CSKA":                     "CSKA Moscow",
    "Spartak":                  "Spartak Moscow",
    "Rubin":                    "Rubin Kazan",
    "Ufa":                      "Ufa",
    "Ural":                     "Ural",
    "Sochi":                    "Sochi",
    "PFC Sochi":                "Sochi",
    "Khimki":                   "Khimki",
    "Arsenal Tula":             "Arsenal Tula",
    "Torpedo Moscow":           "Torpedo Moscow",
    "Baltika":                  "Baltika",
    "Rodina Moscow":            "Rodina Moscow",
    "Dynamo Makhachkala":       "Dynamo Makhachkala",
}


def normalize(name: str) -> str:
    """Return the canonical football-data.co.uk team name, or the original if no alias."""
    return TEAM_ALIASES.get(name, name)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_league_year(session, league: str, year: int) -> list[dict]:
    """
    Fetch match records for one league/year.
    Returns a list of dicts with columns:
        league_code, date, home, away, hg, ag, xg_h, xg_a
    Only isResult=True matches are included.
    """
    url = BASE_URL + API_PATH.format(league=league, year=year)
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/league/{league}/{year}",
    }
    resp = session.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return []

    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        return []

    dates = data.get("dates", [])
    rows = []
    for match in dates:
        if not match.get("isResult"):
            continue
        # Parse datetime to date string YYYY-MM-DD
        raw_dt = match.get("datetime", "")
        try:
            date_str = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        except ValueError:
            date_str = raw_dt[:10]

        rows.append({
            "league_code": league,
            "date":        date_str,
            "home":        match["h"]["title"],
            "away":        match["a"]["title"],
            "hg":          int(match["goals"]["h"]),
            "ag":          int(match["goals"]["a"]),
            "xg_h":        float(match["xG"]["h"]),
            "xg_a":        float(match["xG"]["a"]),
        })
    return rows


def fetch_league(session, league: str, years: list[int] = YEARS) -> list[dict]:
    """Fetch all years for one league and return combined rows."""
    all_rows = []
    for year in years:
        rows = fetch_league_year(session, league, year)
        all_rows.extend(rows)
        if rows:
            print(f"  {league}/{year}: {len(rows)} matches")
        else:
            print(f"  {league}/{year}: 0 matches (season may not exist yet)")
        time.sleep(SLEEP_BETWEEN)
    return all_rows


def save_csv(rows: list[dict], league: str) -> Path:
    """Write rows to CSV and return the path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"understat_{league}.csv"
    fieldnames = ["league_code", "date", "home", "away", "hg", "ag", "xg_h", "xg_a"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


# ---------------------------------------------------------------------------
# Match-rate verification
# ---------------------------------------------------------------------------

def verify_match_rate(
    league: str,
    rows: list[dict],
    ref_csv: str,
    ref_col: str,
    extra_ref_csvs: list[str] = None,
) -> tuple[float, list[str]]:
    """
    Cross-check up to 20 unique team names (after aliasing) against one or more
    reference CSVs.  Returns (fraction_matched, list_of_unmatched_names).

    extra_ref_csvs lets you pass additional season files so promoted/relegated
    clubs from older years are still counted as matched.
    """
    if not rows:
        return 0.0, []

    # Load reference teams from all supplied files
    ref_teams: set[str] = set()
    for path in ([ref_csv] + (extra_ref_csvs or [])):
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                val = row.get(ref_col, "").strip()
                if val:
                    ref_teams.add(val)

    # Collect unique understat names (after alias)
    understat_teams = {normalize(r["home"]) for r in rows} | {normalize(r["away"]) for r in rows}
    sample = sorted(understat_teams)[:20]

    unmatched = [t for t in sample if t not in ref_teams]
    matched = len(sample) - len(unmatched)
    return (matched / len(sample) if sample else 0.0), unmatched


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(leagues: list[str] = None):
    from curl_cffi import requests as crequests

    if leagues is None:
        leagues = LEAGUES

    session = crequests.Session()

    total_rows = 0
    league_counts: dict[str, int] = {}

    for league in leagues:
        print(f"\nFetching {league} ({UNDERSTAT_TO_LEAGUE[league]})...")
        rows = fetch_league(session, league)
        if rows:
            out_path = save_csv(rows, league)
            league_counts[league] = len(rows)
            total_rows += len(rows)
            print(f"  -> Saved {len(rows)} rows to {out_path}")
        else:
            league_counts[league] = 0
            print(f"  -> No data for {league}")

    # Summary
    print("\n" + "="*55)
    print("Row counts per league:")
    for league, count in league_counts.items():
        print(f"  {league:<15} {count:>6} rows")
    print(f"  {'TOTAL':<15} {total_rows:>6} rows")

    # Match-rate verification
    # We use all available E0 files (multiple seasons) so promoted/relegated
    # clubs from older seasons are still counted as matched.
    print("\nTeam-name match rate after aliasing (all available seasons):")
    raw_dir = SCRIPT_DIR.parent / "data" / "raw"
    all_epl_csvs = sorted(str(p) for p in raw_dir.glob("main_*_E0.csv"))
    ref_rus = raw_dir / "extra_RUS.csv"

    epl_rows = []
    rus_rows = []
    for league in leagues:
        csv_path = DATA_DIR / f"understat_{league}.csv"
        if csv_path.exists():
            with open(csv_path, newline="", encoding="utf-8") as f:
                league_rows = list(csv.DictReader(f))
            if league == "EPL":
                epl_rows = league_rows
            elif league == "RFPL":
                rus_rows = league_rows

    if epl_rows and all_epl_csvs:
        primary, *extras = all_epl_csvs
        rate, unmatched = verify_match_rate("EPL", epl_rows, primary, "HomeTeam",
                                            extra_ref_csvs=extras)
        print(f"  EPL  (across {len(all_epl_csvs)} season files): "
              f"{rate*100:.0f}%  unmatched={unmatched or 'none'}")

    if rus_rows:
        rate, unmatched = verify_match_rate("RFPL", rus_rows, str(ref_rus), "Home")
        print(f"  RFPL (extra_RUS.csv):                  "
              f"{rate*100:.0f}%  unmatched={unmatched or 'none'}")

    return league_counts


if __name__ == "__main__":
    leagues_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    if leagues_arg:
        unknown = [l for l in leagues_arg if l not in UNDERSTAT_TO_LEAGUE]
        if unknown:
            print(f"Unknown leagues: {unknown}. Valid: {LEAGUES}", file=sys.stderr)
            sys.exit(1)
    main(leagues_arg)
