"""
fotmob_fetch.py
---------------
Fetches football fixtures from FotMob's undocumented-but-public API.

Endpoint: https://www.fotmob.com/api/data/matches?date=YYYYMMDD
- Returns 200 + JSON without any auth header required.
- No x-mas signing needed (tested: endpoint ignores the header entirely).
- Response is served via CloudFront CDN, cache-control: public, max-age=10.
- x-robots-tag: noindex (FotMob doesn't want it crawled, respect rate limits).
"""

from __future__ import annotations

from curl_cffi import requests as cffi_requests
from datetime import date, datetime, timezone
from typing import Optional, TypedDict


class Fixture(TypedDict):
    league: str
    home: str
    away: str
    time_utc: str   # ISO-8601, e.g. "2026-07-24T18:00:00.000Z"
    match_id: int


_BASE = "https://www.fotmob.com/api/data/matches"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fotmob.com/",
}


def get_fixtures(target_date: Optional[date] = None) -> list[Fixture]:
    """
    Fetch all football fixtures for *target_date* (default: today UTC).

    Returns a list of Fixture dicts sorted by kick-off time.

    Raises:
        RuntimeError  – if the HTTP request fails or the response is not JSON.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    date_str = target_date.strftime("%Y%m%d")
    url = f"{_BASE}?date={date_str}"

    resp = cffi_requests.get(url, headers=_HEADERS, timeout=20, impersonate="chrome110")
    if resp.status_code != 200:
        raise RuntimeError(f"FotMob returned HTTP {resp.status_code} for {url}")

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"FotMob response was not JSON: {exc}") from exc

    fixtures: list[Fixture] = []
    for league_block in payload.get("leagues", []):
        league_name = league_block.get("name", "Unknown League")
        for match in league_block.get("matches", []):
            fixtures.append(
                Fixture(
                    league=league_name,
                    home=match["home"]["name"],
                    away=match["away"]["name"],
                    time_utc=match["status"]["utcTime"],
                    match_id=match["id"],
                )
            )

    fixtures.sort(key=lambda f: f["time_utc"])
    return fixtures


if __name__ == "__main__":
    today = datetime.now(timezone.utc).date()
    print(f"Fetching FotMob fixtures for {today} …")
    try:
        fixtures = get_fixtures(today)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        raise SystemExit(1)

    # Group by league for a readable summary
    from collections import defaultdict
    by_league: dict[str, list[Fixture]] = defaultdict(list)
    for f in fixtures:
        by_league[f["league"]].append(f)

    print(f"\nTotal fixtures : {len(fixtures)}")
    print(f"Leagues        : {len(by_league)}")
    print()
    for league, matches in sorted(by_league.items()):
        print(f"  {league} ({len(matches)} matches)")
        for m in matches[:2]:
            print(f"    {m['home']} vs {m['away']}  @ {m['time_utc']}")
        if len(matches) > 2:
            print(f"    … and {len(matches) - 2} more")
