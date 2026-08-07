"""
transfermarkt_injuries.py
--------------------------
Scrapes current injury lists from Transfermarkt league pages and outputs
a JSON file with structured injury records.

Usage:
    python3 transfermarkt_injuries.py           # scrape all leagues
    python3 transfermarkt_injuries.py GB1 ES1   # scrape specific league codes

Output:
    /agent/workspace/football_predictor/output/injuries.json
    Schema: [{"league_code": str, "team": str, "player": str,
              "injury": str, "return": str}, ...]

Note on team names:
    Transfermarkt uses full club names (e.g. "Manchester City FC", "Real Madrid CF").
    These are stored as-is; callers should apply their own normalisation if needed.
"""

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# League configuration
# ---------------------------------------------------------------------------

# (league_code, url_slug)
LEAGUES: list[tuple[str, str]] = [
    # Active leagues running now (July 2026)
    ("BRA1", "campeonato-brasileiro-serie-a"),
    ("AR1N", "torneo-profesional"),
    ("SE1",  "allsvenskan"),
    ("NO1",  "eliteserien"),
    ("MLS1", "major-league-soccer"),
    ("MEX1", "liga-mx-apertura"),
    ("RU1",  "premier-liga"),
    ("PL1",  "pko-bp-ekstraklasa"),
    ("RO1",  "superliga"),
    ("DK1",  "superligaen"),
    # Big-5 European — pages exist year-round (show pre-season injuries)
    ("GB1",  "premier-league"),
    ("ES1",  "laliga"),
    ("L1",   "bundesliga"),
    ("IT1",  "serie-a"),
    ("FR1",  "ligue-1"),
]

BASE_URL = "https://www.transfermarkt.com"
INJURY_PATH = "/{slug}/verletztespieler/wettbewerb/{code}"

SLEEP_BETWEEN = 2.0  # seconds — be polite

# Output path
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "injuries.json"

# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _parse_injury_table(soup, league_code: str) -> list[dict]:
    """
    Parse the injuries table from a BeautifulSoup page object.
    Returns a list of injury dicts.
    """
    from bs4 import BeautifulSoup  # imported here so bs4 isn't required at module level

    records = []
    tables = soup.find_all("table", class_="items")
    if not tables:
        return records

    # Use the first table (main injury list)
    table = tables[0]
    rows = table.find_all("tr", class_=["odd", "even"])

    for row in rows:
        tds = row.find_all("td", recursive=False)
        if len(tds) < 3:
            continue

        # Player name: first anchor pointing to /profil/spieler/
        player_link = row.find("a", href=lambda h: h and "/profil/spieler/" in h)
        if not player_link:
            continue
        player = player_link.get_text(strip=True)

        # Club: image with class "tiny_wappen" carries the club name in title attr
        club_img = row.find("img", class_="tiny_wappen")
        team = club_img.get("title", "").strip() if club_img else ""

        # Injury type: <td class="links"> contains the injury description
        injury_td = row.find("td", class_="links")
        injury = injury_td.get_text(strip=True) if injury_td else ""

        # Expected return: 4th direct <td> (index 3); empty string if unknown
        return_date = tds[3].get_text(strip=True) if len(tds) >= 4 else ""

        records.append({
            "league_code": league_code,
            "team":        team,
            "player":      player,
            "injury":      injury,
            "return":      return_date,
        })

    return records


def get_injuries(
    leagues: list[tuple[str, str]] = None,
    sleep: float = SLEEP_BETWEEN,
    verbose: bool = True,
) -> list[dict]:
    """
    Scrape injury data for all specified leagues.

    Parameters
    ----------
    leagues : list of (code, slug) tuples, defaults to module-level LEAGUES
    sleep   : seconds to wait between requests
    verbose : print per-league counts

    Returns
    -------
    list of dicts with keys: league_code, team, player, injury, return
    """
    from curl_cffi import requests as crequests
    from bs4 import BeautifulSoup

    if leagues is None:
        leagues = LEAGUES

    session = crequests.Session()
    all_records: list[dict] = []
    skipped: list[str] = []

    for code, slug in leagues:
        url = BASE_URL + INJURY_PATH.format(slug=slug, code=code)
        try:
            resp = session.get(
                url,
                impersonate="chrome",
                headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
                },
                timeout=30,
            )
        except Exception as exc:
            if verbose:
                print(f"  {code}: ERROR ({exc})")
            skipped.append(code)
            continue

        if resp.status_code != 200:
            if verbose:
                print(f"  {code}: HTTP {resp.status_code} — skipped")
            skipped.append(code)
            time.sleep(sleep)
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        records = _parse_injury_table(soup, code)

        if verbose:
            print(f"  {code} ({slug}): {len(records)} injuries")

        all_records.extend(records)
        time.sleep(sleep)

    if verbose and skipped:
        print(f"\n  Skipped leagues (no data / HTTP error): {skipped}")

    return all_records


def save_injuries(records: list[dict], path: Path = OUTPUT_FILE) -> None:
    """Write records to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(codes: list[str] = None):
    valid_codes = {c for c, _ in LEAGUES}

    if codes:
        unknown = [c for c in codes if c not in valid_codes]
        if unknown:
            print(f"Unknown league codes: {unknown}.", file=sys.stderr)
            print(f"Valid codes: {sorted(valid_codes)}", file=sys.stderr)
            sys.exit(1)
        leagues = [(c, s) for c, s in LEAGUES if c in codes]
    else:
        leagues = LEAGUES

    print("Scraping Transfermarkt injury pages...")
    records = get_injuries(leagues=leagues, verbose=True)

    save_injuries(records)

    # Summary
    print("\n" + "="*55)
    print("Injury counts per league:")
    from collections import Counter
    counts = Counter(r["league_code"] for r in records)
    for code, slug in leagues:
        print(f"  {code:<8} {counts.get(code, 0):>4} injured players")
    print(f"\n  TOTAL: {len(records)} injury records")
    print(f"  Output: {OUTPUT_FILE}")

    return records


if __name__ == "__main__":
    codes_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    main(codes_arg)
