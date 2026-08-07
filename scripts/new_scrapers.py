# -*- coding: utf-8 -*-
"""New prediction-site scrapers: tipsgg, goalvertex, soccerstats.

Appends canonical tip dicts (built via `tip()`) to a list passed in.
Import helpers from harvest_sites so the fetch/tip/cs_to_1x2 interface
is reused unchanged.
"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

# Reuse shared helpers from the existing harvester
sys.path.insert(0, os.path.dirname(__file__))
from harvest_sites import fetch, tip, cs_to_1x2

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
TODAY_ISO = TODAY.isoformat()          # YYYY-MM-DD
TOMORROW_ISO = TOMORROW.isoformat()

# tips.gg encodes dates as DD-MM-YYYY in URLs
TODAY_DDMMYYYY = TODAY.strftime("%d-%m-%Y")
TOMORROW_DDMMYYYY = TOMORROW.strftime("%d-%m-%Y")


# ----------------------------------------------------------------- tipsgg
def tipsgg(tips):
    """Scrape https://tips.gg/football/predictions/ (aggregator of 200+ tipsters).

    Parsing approach
    ----------------
    The listing page (fetched once with chrome110 impersonation) renders all
    upcoming matches server-side.  Each `<div class="element match ...">` block
    contains:

    * `<a class="match-link" href="...">` – URL encodes date (DD-MM-YYYY) and
      home-vs-away slug, so home/away team identities can be derived without
      fetching individual match pages.
    * `<div class="winner-name">` – the team with the plurality of aggregated
      tips (predicted winner).  When checked against the home-team slug from the
      URL this yields a 1X2 pick of "1" (home) or "2" (away).  "Draw" picks are
      not shown on the listing page so only 1/X/2 outcomes that correspond to a
      team name can be captured here.
    * `<div class="middle-odd decimal ...">` – best available decimal odds for
      the predicted outcome.
    * `<span class="tournament-name">` – league/competition name.

    Only matches whose date == today or tomorrow are included.
    """
    url = "https://tips.gg/football/predictions/"
    # The listing page is accessible with chrome110; plain 'chrome' returns 403
    profiles = ["chrome110", "chrome124", "chrome", "safari17_0"]
    soup = None
    last_err = None
    for p in profiles:
        try:
            r = cr.get(url, impersonate=p, timeout=30)
            if r.status_code == 200 and len(r.text) >= 5000:
                soup = BeautifulSoup(r.text, "lxml")
                break
            last_err = f"status={r.status_code} len={len(r.text)}"
        except Exception as ex:
            last_err = str(ex)
        time.sleep(1.5)

    if soup is None:
        raise RuntimeError(f"tipsgg listing unreachable: {last_err}")

    seen_keys = set()
    match_els = soup.find_all("div", class_="match")

    for m in match_els:
        try:
            link_el = m.find("a", class_="match-link")
            if not link_el:
                continue
            href = link_el.get("href", "")

            # Parse URL: https://tips.gg/matches/football/DD-MM-YYYY/home-vs-away/HH-MM/predictions/
            url_re = re.match(
                r"https://tips\.gg/matches/football/(\d{2}-\d{2}-\d{4})/([^/]+)/",
                href,
            )
            if not url_re:
                continue

            match_date_str = url_re.group(1)  # DD-MM-YYYY
            if match_date_str not in (TODAY_DDMMYYYY, TOMORROW_DDMMYYYY):
                continue

            team_slug = url_re.group(2)  # e.g. "green-gully-vs-dandenong-city"
            if "-vs-" not in team_slug:
                continue
            home_slug, away_slug = team_slug.split("-vs-", 1)

            # winner-name = team with plurality of aggregated tips
            winner_el = m.find(class_="winner-name")
            vs_el = m.find(class_="vs")
            if not winner_el or not vs_el:
                continue

            winner_name = winner_el.get_text(strip=True)
            vs_text = vs_el.get_text(strip=True)  # "vs AwayTeam"
            away_name = vs_text[3:].strip() if vs_text.lower().startswith("vs ") else vs_text.strip()

            # Determine home team: the winner-name is always one of the two teams;
            # we need to know which is home to assign 1/2.  Cross-reference the
            # winner against the URL slug (home slug comes first).
            winner_slug_norm = winner_name.lower().replace(" ", "-").replace(".", "").replace("'", "")

            # Fuzzy slug match: check if winner name tokens appear in each slug
            def slug_match(name_norm, slug):
                # Accept if ≥ 70% of slug tokens appear in name, or vice-versa
                n_tokens = set(name_norm.replace("-", " ").split())
                s_tokens = set(slug.replace("-", " ").split())
                if not n_tokens or not s_tokens:
                    return False
                common = n_tokens & s_tokens
                return len(common) / max(len(n_tokens), len(s_tokens)) >= 0.5

            if slug_match(winner_slug_norm, home_slug):
                pick = "1"
                home_name = winner_name
            elif slug_match(winner_slug_norm, away_slug):
                pick = "2"
                home_name = vs_el.get_text(strip=True)  # fallback; away is winner
                # Recalculate home from slug
                home_name = home_slug.replace("-", " ").title()
            else:
                # Name mismatch – skip rather than guess
                continue

            # Odds (shown for the predicted outcome)
            odd_el = m.find("div", class_="middle-odd")
            odds = None
            if odd_el:
                try:
                    odds = float(odd_el.get_text(strip=True))
                except ValueError:
                    pass

            # League
            league_el = m.find(class_="tournament-name")
            league = league_el.get_text(strip=True) if league_el else None

            # Use today ISO for both dates (we filter by today/tomorrow above)
            match_iso = (
                TODAY_ISO
                if match_date_str == TODAY_DDMMYYYY
                else TOMORROW_ISO
            )

            key = ("tipsgg", home_name, away_name, "1X2", pick)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            t = tip(
                "tipsgg",
                home_name,
                away_name,
                "1X2",
                pick,
                odds=odds,
                league=league,
                url=href,
            )
            t["match_date"] = match_iso
            tips.append(t)

        except Exception:
            continue


# -------------------------------------------------------------- goalvertex
def goalvertex(tips):
    """Scrape https://www.goalvertex.com/football-predictions-today (and -tomorrow).

    Parsing approach
    ----------------
    GoalVertex renders all match cards server-side.  Each `<article class="match-card">`
    contains:

    * `<div class="teams-name">` – "Home <span class='vs'>vs</span> Away"
    * `<span class="bet-tag bet-*">` – the recommended market/pick
    * `<div class="pred-text">` – human-readable version of the pick
    * `<p class="analysis-text">` – includes "confidence level of N%" → probability
    * `<div class="astat">` – stats block with Odds, probabilities etc.
    * `<div class="card-league">` – league name

    Market normalisation:
      "Home Win"           → 1X2 / "1"
      "Away Win"           → 1X2 / "2"
      "Draw"               → 1X2 / "X"
      "Home or Away Win"   → DC  / "12"
      "Over 2.5"           → OU25 / "Over"
      "Under 2.5"          → OU25 / "Under"
      "BTTS – Yes"         → BTTS / "Yes"
      "BTTS – No"          → BTTS / "No"
    """
    sources = [
        ("https://www.goalvertex.com/football-predictions-today", TODAY_ISO),
        ("https://www.goalvertex.com/football-predictions-tomorrow", TOMORROW_ISO),
    ]

    seen_keys = set()

    for page_url, match_iso in sources:
        try:
            soup = fetch(page_url, imp="chrome")
        except Exception as ex:
            sys.stderr.write(f"goalvertex fetch failed ({page_url}): {ex}\n")
            continue

        cards = soup.find_all("article", class_="match-card")
        for card in cards:
            try:
                # --- Team names ---
                teams_el = card.find(class_="teams-name")
                if not teams_el:
                    continue
                vs_span = teams_el.find("span", class_="vs")
                full_teams = teams_el.get_text(strip=True)
                if vs_span:
                    vs_txt = vs_span.get_text(strip=True)  # "vs"
                    parts = full_teams.split(vs_txt, 1)
                    home = parts[0].strip()
                    away = parts[1].strip() if len(parts) > 1 else ""
                else:
                    # Fallback: split on " vs "
                    parts = re.split(r"\s+vs\s+", full_teams, maxsplit=1, flags=re.IGNORECASE)
                    home = parts[0].strip()
                    away = parts[1].strip() if len(parts) > 1 else ""

                if not home or not away:
                    continue

                # --- Bet tag / market ---
                bet_el = card.find(class_="bet-tag")
                if not bet_el:
                    continue
                bet_text = bet_el.get_text(strip=True)

                market_map = {
                    "Home Win": ("1X2", "1"),
                    "Away Win": ("1X2", "2"),
                    "Draw": ("1X2", "X"),
                    "Home or Away Win": ("DC", "12"),
                    "Over 2.5": ("OU25", "Over"),
                    "Under 2.5": ("OU25", "Under"),
                    "BTTS – Yes": ("BTTS", "Yes"),
                    "BTTS – No": ("BTTS", "No"),
                    # Handle minor text variants
                    "BTTS - Yes": ("BTTS", "Yes"),
                    "BTTS - No": ("BTTS", "No"),
                }
                if bet_text not in market_map:
                    continue
                market, pick = market_map[bet_text]

                # --- Probability ---
                prob = None
                analysis_el = card.find(class_="analysis-text")
                if analysis_el:
                    prob_m = re.search(r"confidence level of (\d+)%", analysis_el.get_text())
                    if prob_m:
                        prob = int(prob_m.group(1)) / 100.0

                # --- Odds ---
                odds = None
                for astat in card.find_all(class_="astat"):
                    lbl = astat.find(class_="astat-lbl")
                    val = astat.find(class_="astat-val")
                    if lbl and val and lbl.get_text(strip=True) == "Odds":
                        raw = val.get_text(strip=True)
                        if raw and raw != "N/A":
                            try:
                                odds = float(raw)
                            except ValueError:
                                pass

                # --- League ---
                league_el = card.find(class_="card-league")
                league = league_el.get_text(strip=True) if league_el else None

                key = ("goalvertex", home, away, market, pick)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                t = tip(
                    "goalvertex",
                    home,
                    away,
                    market,
                    pick,
                    prob=prob,
                    odds=odds,
                    league=league,
                    url=page_url,
                )
                t["match_date"] = match_iso
                tips.append(t)

            except Exception:
                continue

        time.sleep(1.0)


# --------------------------------------------------------------- soccerstats
def soccerstats(tips):
    """Attempt to extract explicit predictions from https://www.soccerstats.com/.

    NOTE: After live inspection (2026-07-24) soccerstats.com does NOT publish
    explicit per-match picks, probability percentages, or directional predictions
    on its homepage or today's-matches section.

    The "Playing today" block on the homepage is a single blog-style editorial
    preview (narrative text only, no stated pick or confidence figure).  There
    are no prediction columns, leaning indicators, or %-based picks anywhere in
    the fetched HTML.

    Per the task requirements ("DO NOT synthesize one"), this function leaves
    `tips` unmodified and returns immediately.
    """
    # No explicit prediction content to parse — returning empty is correct behaviour.
    return


# ---------------------------------------------------------------- dedupe util
def _dedupe(tip_list):
    seen, out = set(), []
    for t in tip_list:
        k = (t["source"], t["home"], t["away"], t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def run():
    """Harvest the three new sites and write output/tips_newsites.json."""
    os.makedirs(OUT, exist_ok=True)
    all_tips, summary = [], {}
    for fn in (tipsgg, goalvertex, soccerstats):
        before = len(all_tips)
        try:
            fn(all_tips)
            summary[fn.__name__] = len(all_tips) - before
        except Exception as ex:
            summary[fn.__name__] = f"FAILED: {ex}"
            print(f"{fn.__name__}: FAILED – {ex}", file=sys.stderr)
    deduped = _dedupe(all_tips)
    out_path = os.path.join(OUT, "tips_newsites.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=1, ensure_ascii=False)
    print(json.dumps({"per_site": summary, "total": len(deduped)}, ensure_ascii=False))
    return deduped


# ---------------------------------------------------------------- __main__
if __name__ == "__main__":
    run()
