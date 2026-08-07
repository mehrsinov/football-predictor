"""
betmines_fetch.py — scraper for betmines.com football predictions.

Extraction method: Direct JSON API at https://api.betmines.com/betmines/v1/
Endpoint: fixtures/web?dateFormat=extended&platform=website
Params:  from=<ISO datetime>, to=<ISO datetime>

No authentication required (public CORS-open API). The site is a Nuxt.js SSR
app whose frontend fetches predictions via this REST API at page load.
Discovered by inspecting the Webpack bundle at /_nuxt/app.*.js.

API prediction field mapping (native → canonical):
  "1"   → 1X2 / "1"
  "X"   → 1X2 / "X"
  "2"   → 1X2 / "2"
  "1X"  → DC  / "1X"
  "X2"  → DC  / "X2"
  "12"  → DC  / "12"
  "O15" → OU15 / "Over"
  "U15" → OU15 / "Under"
  "O25" → OU25 / "Over"
  "U25" → OU25 / "Under"
  "O35" → OU35 / "Over"
  "U35" → OU35 / "Under"
  "GG"  → BTTS / "Yes"
  "NG"  → BTTS / "No"
"""

import json
import os
import datetime
from curl_cffi import requests as cffi_requests

API_BASE = "https://api.betmines.com/betmines/v1/"
SITE_URL = "https://betmines.com/football-predictions-today"
# Write next to the other harvesters' output so merge_rank's glob("tips_*.json")
# actually picks it up. The previous absolute /agent/... path only existed in the
# original dev container, so on GitHub Actions every betmines tip was silently lost.
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "tips_betmines.json")

# Today's date, computed at run time (UTC). Previously hardcoded to a fixed day,
# which meant the scraper kept fetching the same past date's fixtures forever.
TODAY = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

# Mapping from betmines native prediction code → (market, pick) in canonical schema
_PREDICTION_MAP = {
    "1":   ("1X2", "1"),
    "X":   ("1X2", "X"),
    "2":   ("1X2", "2"),
    "1X":  ("DC",  "1X"),
    "X2":  ("DC",  "X2"),
    "12":  ("DC",  "12"),
    "O15": ("OU15", "Over"),
    "U15": ("OU15", "Under"),
    "O25": ("OU25", "Over"),
    "U25": ("OU25", "Under"),
    "O35": ("OU35", "Over"),
    "U35": ("OU35", "Under"),
    "GG":  ("BTTS", "Yes"),
    "NG":  ("BTTS", "No"),
}

# Which probability key to read for each market/pick combo
_PROB_KEY = {
    ("1X2",  "1"):    "home",
    ("1X2",  "X"):    "draw",
    ("1X2",  "2"):    "away",
    ("DC",   "1X"):   "home_draw",
    ("DC",   "X2"):   "draw_away",
    ("DC",   "12"):   "home_away",
    ("OU15", "Over"): "over_1_5",
    ("OU15", "Under"):"under_1_5",
    ("OU25", "Over"): "over_2_5",
    ("OU25", "Under"):"under_2_5",
    ("OU35", "Over"): "over_3_5",
    ("OU35", "Under"):"under_3_5",
    ("BTTS", "Yes"):  "btts",
    ("BTTS", "No"):   "btts_no",
}

# Which odds field to read for each market/pick combo
_ODDS_KEY = {
    ("1X2",  "1"):    "odd1",
    ("1X2",  "X"):    "oddx",
    ("1X2",  "2"):    "odd2",
    ("DC",   "1X"):   "odd1x",
    ("DC",   "X2"):   "oddx2",
    ("DC",   "12"):   "odd12",
    ("OU15", "Over"): None,          # not in API for 1.5
    ("OU15", "Under"):None,
    ("OU25", "Over"): "oddOver25",
    ("OU25", "Under"):"oddUnder25",
    ("OU35", "Over"): None,
    ("OU35", "Under"):None,
    ("BTTS", "Yes"):  "oddGoal",
    ("BTTS", "No"):   "oddNoGoal",
}


def _make_tip(home, away, market, pick, prob_pct, odds, league, url):
    """Return a canonical tip dict matching the shared schema."""
    return {
        "source":      "betmines",
        "source_type": "site",
        "lang":        "en",
        "league":      league,
        "home":        home,
        "away":        away,
        "match_date":  TODAY,
        "market":      market,
        "pick":        pick,
        "prob":        round(prob_pct / 100, 4) if prob_pct is not None else None,
        "odds":        float(odds) if odds is not None else None,
        "url":         url,
    }


def _fetch_fixtures(from_dt: str, to_dt: str):
    """Fetch fixtures from betmines API for the given UTC ISO datetime range."""
    url = (
        API_BASE
        + "fixtures/web"
        + f"?dateFormat=extended&platform=website"
        + f"&from={from_dt}&to={to_dt}"
    )
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://betmines.com/",
        "Origin": "https://betmines.com",
    }
    resp = cffi_requests.get(url, impersonate="chrome", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def betmines(tips: list) -> None:
    """
    Fetch betmines.com predictions for today (computed at run time) and append
    canonical tip dicts to *tips*.

    Uses the public REST API at https://api.betmines.com/betmines/v1/
    Endpoint: fixtures/web?dateFormat=extended&platform=website
    with from/to UTC ISO datetime parameters.

    Each fixture carries a single 'prediction' field (e.g. "1", "O25", "GG")
    and a 'probability' dict with per-market probabilities (0-100 integers).
    """
    today = TODAY
    from_dt = f"{today}T00:00:00Z"
    to_dt   = f"{today}T23:59:59Z"

    try:
        fixtures = _fetch_fixtures(from_dt, to_dt)
    except Exception as exc:
        print(f"[betmines] ERROR fetching fixtures: {exc}")
        return

    if not isinstance(fixtures, list):
        print(f"[betmines] Unexpected API response type: {type(fixtures)}")
        return

    added = 0
    for fix in fixtures:
        try:
            pred_code = fix.get("prediction")
            if not pred_code or pred_code not in _PREDICTION_MAP:
                continue

            market, pick = _PREDICTION_MAP[pred_code]

            home = fix.get("localTeam", {}).get("name", "").strip()
            away = fix.get("visitorTeam", {}).get("name", "").strip()
            if not home or not away:
                continue

            league = fix.get("league", {}).get("name")

            # Probability (0-100 int → 0-1 float)
            prob_dict = fix.get("probability") or {}
            prob_key = _PROB_KEY.get((market, pick))
            prob_pct = prob_dict.get(prob_key) if prob_key else None

            # Odds (native float or None)
            odds_key = _ODDS_KEY.get((market, pick))
            odds = fix.get(odds_key) if odds_key else None

            tip = _make_tip(home, away, market, pick, prob_pct, odds, league, SITE_URL)
            tips.append(tip)
            added += 1

        except Exception as row_exc:
            # Per-row safety: skip bad rows without killing the whole run
            print(f"[betmines] Skipping row due to error: {row_exc}")
            continue

    print(f"[betmines] {added} tips extracted from {len(fixtures)} fixtures")


def run():
    """Fetch predictions, write JSON output, print summary."""
    tips: list = []
    betmines(tips)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(tips, fh, indent=2, ensure_ascii=False)

    print(f"Written {len(tips)} tips to {OUTPUT_PATH}")

    if tips:
        print("\nSample tips (first 3):")
        for t in tips[:3]:
            print(
                f"  {t['home']} vs {t['away']}"
                f" | {t['market']}={t['pick']}"
                f" | prob={t['prob']}"
                f" | odds={t['odds']}"
                f" | league={t['league']}"
            )


if __name__ == "__main__":
    run()
