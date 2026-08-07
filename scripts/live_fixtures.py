# -*- coding: utf-8 -*-
"""Global live-fixture layer — a strict-priority waterfall across many sources.

Goal: never again "fixtures=0" or stale match dates. We query a prioritized list
of worldwide fixture sources, merge/dedupe, and produce TWO artifacts:

  A) output/all_fixtures.json (+ .md) — the COMPREHENSIVE list of every upcoming
     competition worldwide we could find, with official competition name, country,
     both kickoff times (UTC + Tehran local), status, URL and the source used.

  B) load_live_fixtures(days_ahead) — the subset whose competition maps to a
     league our Dixon-Coles model has training history for, in the internal
     14-column fixture schema, so predict_today can actually price them.

Priority order (exactly the user's spec; edit SOURCES to reorder):
  1 Sports Mole  2 AiScore  3 Sofascore  4 ESPN  5 365scores
  6 Livescore    7 Flashscore  8 FotMob  9 prexzy / any other reliable source

Hard rules baked into the design:
  * Every source is fault-isolated: a failing/blocked/incomplete source returns
    [] and we continue down the list. We never stop after one source.
  * We NEVER fabricate a fixture. A parser that doesn't recognize a response
    yields nothing — it never emits a guessed match. (Every field comes via
    .get(); a record missing home/away/kickoff is dropped, not invented.)
  * Duplicate matches across sources are merged; the higher-priority source wins
    the record and lower ones are recorded in also_sources. If Sports Mole and
    AiScore disagree on a merged match, we keep both and flag discrepancy=True.

Parser confidence (see each fetch_* docstring):
  CONFIRMED shape  -> sofascore, espn, fotmob (fotmob parser migrated verbatim
                      from fixtures_merge.py, which is already in production).
  BEST-EFFORT      -> 365scores, livescore, prexzy, sportsmole: implemented
                      against the documented/expected endpoint but guarded to
                      return [] on any shape mismatch, pending a Step-0 live probe.
  STUB (probe TODO)-> aiscore, flashscore: return [] until their DOM/feed is
                      inspected live; wired into the waterfall so adding the
                      parser later needs no other change.
"""
import json
import os
import sys

import pandas as pd

try:
    from curl_cffi import requests as cr
    def _get(url, impersonate="chrome124", headers=None, timeout=20):
        return cr.get(url, impersonate=impersonate, headers=headers or {}, timeout=timeout)
except Exception:  # pragma: no cover - curl_cffi is a repo dependency
    import urllib.request
    def _get(url, impersonate=None, headers=None, timeout=20):
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
        class _R:
            def __init__(self, resp):
                self._b = resp.read(); self.status_code = resp.status
                self.headers = dict(resp.headers)
            def json(self): return json.loads(self._b)
            @property
            def text(self): return self._b.decode("utf-8", "replace")
            @property
            def content(self): return self._b
        return _R(urllib.request.urlopen(req, timeout=timeout))

# Reuse the model's fuzzy matcher and the long->short team alias map so live
# names ("Wolverhampton Wanderers") line up with football-data labels ("Wolves").
try:
    from merge_rank import team_sim
except Exception:
    def team_sim(a, b):
        return 100 if str(a).strip().lower() == str(b).strip().lower() else 0

try:
    from understat_fetch import normalize as alias_normalize
except Exception:
    def alias_normalize(x):
        return x

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
TEHRAN = "Asia/Tehran"


def _log(msg):
    sys.stderr.write(f"[live_fixtures] {msg}\n")


# ---------------------------------------------------------------------------
# league mapping: (country_keyword, competition_keyword) -> our history label.
# Keyed on BOTH because competition names collide across countries ("Serie A" =
# Italy AND Brazil; "Premier League" = England AND Russia; "Super League" =
# Greece AND China). Second-tier / more specific rules come first so their
# keyword isn't shadowed by a first-tier substring ("laliga" ⊂ "laliga 2").
# Only leagues our model can have history for are listed; anything else -> None
# -> comprehensive artifact only, no prediction (honest coverage boundary).
# ---------------------------------------------------------------------------
LEAGUE_RULES = [
    ("england", "championship", "England Championship"),
    ("england", "league one", "England League One"),
    ("england", "league two", "England League Two"),
    ("england", "premier league", "England Premier League"),
    ("scotland", "championship", "Scotland Championship"),
    ("scotland", "premiership", "Scotland Premiership"),
    ("germany", "2. bundesliga", "Germany 2. Bundesliga"),
    ("germany", "2 bundesliga", "Germany 2. Bundesliga"),
    ("germany", "bundesliga", "Germany Bundesliga"),
    ("spain", "laliga 2", "Spain Segunda"),
    ("spain", "laliga2", "Spain Segunda"),
    ("spain", "segunda", "Spain Segunda"),
    ("spain", "hypermotion", "Spain Segunda"),
    ("spain", "laliga", "Spain La Liga"),
    ("spain", "la liga", "Spain La Liga"),
    ("spain", "primera", "Spain La Liga"),
    ("italy", "serie b", "Italy Serie B"),
    ("italy", "serie a", "Italy Serie A"),
    ("france", "ligue 2", "France Ligue 2"),
    ("france", "ligue 1", "France Ligue 1"),
    ("netherlands", "eredivisie", "Netherlands Eredivisie"),
    ("belgium", "pro league", "Belgium Pro League"),
    ("belgium", "jupiler", "Belgium Pro League"),
    ("portugal", "liga portugal", "Portugal Liga"),
    ("portugal", "primeira", "Portugal Liga"),
    ("portugal", "liga", "Portugal Liga"),
    ("turkey", "super lig", "Turkey Super Lig"),
    ("turkey", "süper lig", "Turkey Super Lig"),
    ("greece", "super league", "Greece Super League"),
    ("brazil", "serie a", "Brazil Serie A"),
    ("brazil", "brasileiro", "Brazil Serie A"),
    ("argentina", "liga profesional", "Argentina Liga Profesional"),
    ("argentina", "primera", "Argentina Liga Profesional"),
    ("sweden", "allsvenskan", "Sweden Allsvenskan"),
    ("norway", "eliteserien", "Norway Eliteserien"),
    ("finland", "veikkausliiga", "Finland Veikkausliiga"),
    ("denmark", "superliga", "Denmark Superliga"),
    ("usa", "mls", "USA MLS"),
    ("united states", "mls", "USA MLS"),
    ("mexico", "liga mx", "Mexico Liga MX"),
    ("mexico", "liga de expansi", "Mexico Liga MX"),
    ("japan", "j1", "Japan J1 League"),
    ("japan", "j.league", "Japan J1 League"),
    ("japan", "j league", "Japan J1 League"),
    ("china", "super league", "China Super League"),
    ("poland", "ekstraklasa", "Poland Ekstraklasa"),
    ("romania", "superliga", "Romania Superliga"),
    ("romania", "liga 1", "Romania Superliga"),
    ("russia", "premier league", "Russia Premier League"),
]


def map_league(country, comp):
    c = (country or "").lower()
    k = (comp or "").lower()
    for ck, kk, label in LEAGUE_RULES:
        if ck in c and kk in k:
            return label
    return None


# ESPN uses country-prefixed league slugs; map the ones our model covers.
ESPN_SLUGS = {
    "eng.1": ("England Premier League", "England"),
    "eng.2": ("England Championship", "England"),
    "eng.3": ("England League One", "England"),
    "eng.4": ("England League Two", "England"),
    "sco.1": ("Scotland Premiership", "Scotland"),
    "sco.2": ("Scotland Championship", "Scotland"),
    "ger.1": ("Germany Bundesliga", "Germany"),
    "ger.2": ("Germany 2. Bundesliga", "Germany"),
    "esp.1": ("Spain La Liga", "Spain"),
    "esp.2": ("Spain Segunda", "Spain"),
    "ita.1": ("Italy Serie A", "Italy"),
    "ita.2": ("Italy Serie B", "Italy"),
    "fra.1": ("France Ligue 1", "France"),
    "fra.2": ("France Ligue 2", "France"),
    "ned.1": ("Netherlands Eredivisie", "Netherlands"),
    "bel.1": ("Belgium Pro League", "Belgium"),
    "por.1": ("Portugal Liga", "Portugal"),
    "tur.1": ("Turkey Super Lig", "Turkey"),
    "gre.1": ("Greece Super League", "Greece"),
    "usa.1": ("USA MLS", "USA"),
    "mex.1": ("Mexico Liga MX", "Mexico"),
    "bra.1": ("Brazil Serie A", "Brazil"),
    "arg.1": ("Argentina Liga Profesional", "Argentina"),
    "rus.1": ("Russia Premier League", "Russia"),
    "jpn.1": ("Japan J1 League", "Japan"),
    "chn.1": ("China Super League", "China"),
    "swe.1": ("Sweden Allsvenskan", "Sweden"),
    "nor.1": ("Norway Eliteserien", "Norway"),
    "den.1": ("Denmark Superliga", "Denmark"),
    "fin.1": ("Finland Veikkausliiga", "Finland"),
    "pol.1": ("Poland Ekstraklasa", "Poland"),
    "rou.1": ("Romania Superliga", "Romania"),
}

# FotMob country codes -> display name (for the comprehensive artifact only).
FOTMOB_CC = {
    "ENG": "England", "ESP": "Spain", "GER": "Germany", "ITA": "Italy",
    "FRA": "France", "NED": "Netherlands", "BEL": "Belgium", "POR": "Portugal",
    "TUR": "Turkey", "GRE": "Greece", "SCO": "Scotland", "USA": "USA",
    "MEX": "Mexico", "BRA": "Brazil", "ARG": "Argentina", "RUS": "Russia",
    "JPN": "Japan", "CHN": "China", "SWE": "Sweden", "NOR": "Norway",
    "DEN": "Denmark", "FIN": "Finland", "POL": "Poland", "ROU": "Romania",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fetch_dates(days_ahead):
    """Calendar dates to query per-date APIs.

    We over-fetch one day either side of the Tehran window because per-date
    endpoints key on UTC (or their own tz); a match at 01:00 Tehran is the
    previous UTC day. The final window filter in collect_all() (on Tehran-local
    date) trims anything that spilled outside [ref, ref+days_ahead).
    """
    ref = pd.Timestamp.now(tz=TEHRAN).tz_localize(None).normalize()
    return [(ref + pd.Timedelta(days=o)).date() for o in range(-1, days_ahead + 1)]


def _mk_record(comp, country, home, away, ts_unix, status, url):
    """Build one full fixture record from a UTC unix timestamp; None if invalid."""
    try:
        utc = pd.Timestamp(int(ts_unix), unit="s", tz="UTC")
    except (TypeError, ValueError, OverflowError):
        return None
    home = str(home or "").strip()
    away = str(away or "").strip()
    if not home or not away:
        return None
    loc = utc.tz_convert(TEHRAN)
    return {
        "competition": (str(comp).strip() or "Unknown"),
        "country": str(country or "").strip(),
        "home": home, "away": away,
        "kickoff_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kickoff_local": loc.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": status or "notstarted",
        "url": str(url or "").strip(),
        "_ts": int(ts_unix),
        "_local_date": loc.tz_localize(None).normalize(),
        "_time": loc.strftime("%H:%M"),
    }


def _iso_to_unix(iso):
    """Parse an ISO-8601 UTC string ('...Z' or with offset) to unix seconds."""
    try:
        t = pd.Timestamp(iso)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return int(t.timestamp())
    except Exception:
        return None


def _same(ah, aa, bh, ba):
    return min(team_sim(ah, bh), team_sim(aa, ba)) >= 80


# ---------------------------------------------------------------------------
# SOURCE 3 — Sofascore  [CONFIRMED shape]  the comprehensive backbone.
#   GET /api/v1/sport/football/scheduled-events/{YYYY-MM-DD} -> {"events":[...]}
#   event: tournament{name, category{name=country}, uniqueTournament{name}},
#          homeTeam{name}, awayTeam{name}, startTimestamp (unix s), status{type}.
# ---------------------------------------------------------------------------
def fetch_sofascore(days_ahead=2):
    out = []
    hdr = {"Referer": "https://www.sofascore.com/", "Origin": "https://www.sofascore.com"}
    for day in _fetch_dates(days_ahead):
        url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{day.isoformat()}"
        try:
            r = _get(url, impersonate="chrome124", headers=hdr)
            if r.status_code != 200:
                continue
            events = (r.json() or {}).get("events", [])
        except Exception:
            continue
        for e in events:
            try:
                if (e.get("status") or {}).get("type") != "notstarted":
                    continue
                t = e.get("tournament") or {}
                ut = t.get("uniqueTournament") or {}
                comp = ut.get("name") or t.get("name") or ""
                country = (t.get("category") or {}).get("name") or ""
                slug = e.get("slug") or ""
                cid = e.get("customId") or ""
                url_m = f"https://www.sofascore.com/{slug}/{cid}" if slug and cid else "https://www.sofascore.com/"
                rec = _mk_record(comp, country,
                                 (e.get("homeTeam") or {}).get("name"),
                                 (e.get("awayTeam") or {}).get("name"),
                                 e.get("startTimestamp"), "notstarted", url_m)
                if rec:
                    out.append(rec)
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 4 — ESPN  [CONFIRMED shape]  per covered-league scoreboard.
#   GET site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=YYYYMMDD
# ---------------------------------------------------------------------------
def fetch_espn(days_ahead=2):
    out = []
    days = [d.strftime("%Y%m%d") for d in _fetch_dates(days_ahead)]
    for slug, (comp, country) in ESPN_SLUGS.items():
        for ymd in days:
            url = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}"
                   f"/scoreboard?dates={ymd}")
            try:
                r = _get(url, impersonate="chrome120")
                if r.status_code != 200:
                    continue
                data = r.json() or {}
            except Exception:
                continue
            for e in data.get("events", []):
                try:
                    comp_o = (e.get("competitions") or [{}])[0]
                    state = (((e.get("status") or {}).get("type")) or {}).get("state")
                    if state and state != "pre":
                        continue
                    cs = comp_o.get("competitors") or []
                    home = away = None
                    for c in cs:
                        nm = (c.get("team") or {}).get("displayName") or (c.get("team") or {}).get("name")
                        if c.get("homeAway") == "home":
                            home = nm
                        elif c.get("homeAway") == "away":
                            away = nm
                    ts = _iso_to_unix(e.get("date"))
                    link = ""
                    for lk in (e.get("links") or []):
                        if "href" in lk:
                            link = lk["href"]; break
                    rec = _mk_record(comp, country, home, away, ts, "notstarted", link)
                    if rec:
                        out.append(rec)
                except Exception:
                    continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 8 — FotMob  [CONFIRMED shape]  parser migrated verbatim from the
#   already-in-production fixtures_merge.py (_fotmob_day). Broad coverage, no odds.
# ---------------------------------------------------------------------------
def fetch_fotmob(days_ahead=2):
    out = []
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
           "Referer": "https://www.fotmob.com/"}
    for day in _fetch_dates(days_ahead):
        url = f"https://www.fotmob.com/api/data/matches?date={day.strftime('%Y%m%d')}"
        try:
            r = _get(url, impersonate="chrome110", headers=hdr)
            if r.status_code != 200:
                continue
            data = r.json() or {}
        except Exception:
            continue
        for lb in data.get("leagues", []):
            comp = lb.get("name") or ""
            country = FOTMOB_CC.get(lb.get("ccode"), lb.get("ccode") or "")
            for m in lb.get("matches", []):
                try:
                    st = m.get("status", {}) or {}
                    if st.get("finished") or st.get("started"):
                        continue
                    ts = _iso_to_unix(st.get("utcTime"))
                    mid = m.get("id")
                    link = f"https://www.fotmob.com/matches/x/{mid}" if mid else "https://www.fotmob.com/"
                    rec = _mk_record(comp, country,
                                     (m.get("home") or {}).get("name"),
                                     (m.get("away") or {}).get("name"),
                                     ts, "notstarted", link)
                    if rec:
                        out.append(rec)
                except Exception:
                    continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 5 — 365scores  [BEST-EFFORT]  documented endpoint; guarded, [] on mismatch.
#   GET webws.365scores.com/web/games/allscores/?...&startDate=DD/MM/YYYY&endDate=...
#   Fields vary; we read defensively and drop anything we can't map. Confirm in Step-0.
# ---------------------------------------------------------------------------
def fetch_365scores(days_ahead=2):
    out = []
    dates = _fetch_dates(days_ahead)
    d0, d1 = dates[0], dates[-1]
    url = ("https://webws.365scores.com/web/games/allscores/"
           "?appTypeId=5&langId=1&timezoneName=Asia/Tehran&userCountryId=107"
           f"&sports=1&startDate={d0.strftime('%d/%m/%Y')}&endDate={d1.strftime('%d/%m/%Y')}")
    try:
        r = _get(url, impersonate="chrome124",
                 headers={"Referer": "https://www.365scores.com/"})
        if r.status_code != 200:
            return out
        data = r.json() or {}
    except Exception:
        return out
    comps = {c.get("id"): c for c in data.get("competitions", []) if isinstance(c, dict)}
    countries = {c.get("id"): c for c in data.get("countries", []) if isinstance(c, dict)}
    # Step-0 probe showed statusGroup is not a reliable "scheduled" flag (sample
    # had statusGroup=4 statusText='Ended'), so filter on the self-describing
    # statusText/shortStatusText strings instead: drop anything already live or
    # finished; keep scheduled/not-started. competitionDisplayName is on the game
    # itself (no competitions[] join needed), with country via competition.countryId.
    _DONE_OR_LIVE = ("end", "ft", "final", "abandon", "postpon", "cancel", "susp",
                     "half", "'", "live", "break")
    for g in data.get("games", []):
        try:
            st = (str(g.get("statusText") or "") + " " + str(g.get("shortStatusText") or "")).lower()
            if g.get("justEnded") or any(m in st for m in _DONE_OR_LIVE):
                continue
            comp = comps.get(g.get("competitionId"), {})
            comp_name = g.get("competitionDisplayName") or comp.get("name") or ""
            country = countries.get(comp.get("countryId"), {}).get("name") or ""
            ts = _iso_to_unix(g.get("startTime"))
            home = (g.get("homeCompetitor") or {}).get("name")
            away = (g.get("awayCompetitor") or {}).get("name")
            rec = _mk_record(comp_name, country, home, away, ts, "notstarted",
                             "https://www.365scores.com/")
            if rec:
                out.append(rec)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 6 — Livescore  [BEST-EFFORT]  documented CDN feed; guarded, [] on mismatch.
#   GET prod-cdn-mev-api.livescore.com/v1/api/app/date/soccer/{YYYYMMDD}/0
# ---------------------------------------------------------------------------
def fetch_livescore(days_ahead=2):
    out = []
    for day in _fetch_dates(days_ahead):
        url = ("https://prod-cdn-mev-api.livescore.com/v1/api/app/date/soccer/"
               f"{day.strftime('%Y%m%d')}/0")
        try:
            r = _get(url, impersonate="chrome124",
                     headers={"Referer": "https://www.livescore.com/"})
            if r.status_code != 200:
                continue
            data = r.json() or {}
        except Exception:
            continue
        for stage in data.get("Stages", []):
            country = stage.get("Cnm") or ""      # country name
            comp = stage.get("Snm") or ""         # stage/competition name
            for ev in stage.get("Events", []):
                try:
                    # Eps = event phase/status string; only keep not-started ("NS").
                    # Esd = start datetime as int yyyymmddHHMMSS in the requested tz.
                    if str(ev.get("Eps", "")).upper() not in ("NS", ""):
                        continue
                    esd = str(ev.get("Esd", ""))
                    ts = None
                    if len(esd) == 14:
                        # Esd is a compact datetime in the tz we requested via the
                        # trailing "/0" in the URL, i.e. UTC. Confirmed by probe:
                        # the same Wolves match reads Esd=...184500 while fotmob and
                        # prexzy both report utcTime 18:45:00Z — so Esd == UTC, NOT
                        # Tehran. (Parsing it as Tehran shifted kickoffs by 3.5h.)
                        dt = pd.to_datetime(esd, format="%Y%m%d%H%M%S", errors="coerce")
                        if pd.notna(dt):
                            ts = int(dt.tz_localize("UTC").timestamp())
                    home = (ev.get("T1") or [{}])[0].get("Nm")
                    away = (ev.get("T2") or [{}])[0].get("Nm")
                    rec = _mk_record(comp, country, home, away, ts, "notstarted",
                                     "https://www.livescore.com/")
                    if rec:
                        out.append(rec)
                except Exception:
                    continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 9 — prexzy  [BEST-EFFORT]  user-supplied API, shape unknown until Step-0.
#   We probe common container/field names and drop anything unrecognized. This
#   NEVER fabricates: no recognizable (home, away, kickoff) -> no record.
# ---------------------------------------------------------------------------
def fetch_prexzy(days_ahead=2):
    """Confirmed shape (Step-0 probe): {"data": [ {competition, area, matches:[
       {status, rawStatus, startTime(ISO-Z), homeTeam(str), awayTeam(str)} ]} ]}.
    'area' is the country ("Inggris Raya" — note the edition=id feed localizes
    country names to Indonesian; competition names stay English)."""
    out = []
    try:
        r = _get("https://prexzyapis.com/sports/goallivescore", impersonate="chrome124")
        if r.status_code != 200:
            return out
        data = r.json()
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for comp_block in data.get("data", []):
        if not isinstance(comp_block, dict):
            continue
        comp = comp_block.get("competition") or ""
        country = comp_block.get("area") or ""
        for m in comp_block.get("matches", []):
            try:
                if not isinstance(m, dict):
                    continue
                raw = str(m.get("rawStatus") or "").upper()
                status = str(m.get("status") or "").lower()
                # keep only not-yet-started matches
                if raw and raw != "FIXTURE":
                    continue
                if not raw and "not started" not in status:
                    continue
                ts = _iso_to_unix(m.get("startTime"))
                rec = _mk_record(comp, country, m.get("homeTeam"), m.get("awayTeam"),
                                 ts, "notstarted", "https://prexzyapis.com/")
                if rec:
                    out.append(rec)
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# SOURCE 1 — Sports Mole  [STUB — Step-0 DOM probe required]
#   HTML preview index. Reliable extraction needs the live DOM structure, which
#   the classifier outage currently blocks. Returns [] (never a guessed match)
#   until Step-0 pins down the selector. Wired in so only this body changes later.
# ---------------------------------------------------------------------------
def fetch_sportsmole(days_ahead=2):
    return []


# ---------------------------------------------------------------------------
# SOURCE 2 — AiScore  [STUB — Step-0 probe required]  m.aiscore.com/en feed.
# ---------------------------------------------------------------------------
def fetch_aiscore(days_ahead=2):
    return []


# ---------------------------------------------------------------------------
# SOURCE 7 — Flashscore  [STUB — Step-0 probe required]  obfuscated feed.
# ---------------------------------------------------------------------------
def fetch_flashscore(days_ahead=2):
    return []


# Priority order — index = precedence on merge (lower wins). Reorder freely.
SOURCES = [
    ("sportsmole", fetch_sportsmole),
    ("aiscore", fetch_aiscore),
    ("sofascore", fetch_sofascore),
    ("espn", fetch_espn),
    ("365scores", fetch_365scores),
    ("livescore", fetch_livescore),
    ("flashscore", fetch_flashscore),
    ("fotmob", fetch_fotmob),
    ("prexzy", fetch_prexzy),
]


def _public(c):
    """Strip internal (_-prefixed) fields for the comprehensive JSON artifact."""
    rec = {k: v for k, v in c.items() if not k.startswith("_") and k != "_contrib"}
    rec.setdefault("also_sources", [])
    return rec


def collect_all(days_ahead=2, write=True):
    """Run the full waterfall. Returns (comprehensive_records, feed_rows).

    comprehensive_records -> output A schema (competition/country/teams/kickoffs/
                             status/url/source/also_sources[/discrepancy]).
    feed_rows             -> output B, internal 14-column schema, only for
                             mapped (model-covered) leagues, odds all None.
    """
    ref = pd.Timestamp.now(tz=TEHRAN).tz_localize(None).normalize()
    hi = ref + pd.Timedelta(days=days_ahead)

    raw = []
    for prio, (name, fn) in enumerate(SOURCES):
        try:
            recs = fn(days_ahead) or []
        except Exception as ex:
            _log(f"{name}: ERROR {type(ex).__name__}: {ex}")
            recs = []
        kept = [r for r in recs if r and ref <= r["_local_date"] < hi]
        for r in kept:
            r["source"] = name
            r["_prio"] = prio
        _log(f"{name}: {len(kept)} upcoming fixtures in window")
        raw.extend(kept)

    # merge duplicates across sources; higher priority (lower _prio) wins.
    clusters = []
    for r in sorted(raw, key=lambda x: x["_prio"]):
        hit = None
        for c in clusters:
            if c["_local_date"] == r["_local_date"] and _same(c["home"], c["away"], r["home"], r["away"]):
                hit = c
                break
        if hit:
            if r["source"] != hit["source"] and r["source"] not in hit["also_sources"]:
                hit["also_sources"].append(r["source"])
            hit["_contrib"].append((r["source"], r["competition"]))
        else:
            c = dict(r)
            c["also_sources"] = []
            c["_contrib"] = [(r["source"], r["competition"])]
            clusters.append(c)

    # discrepancy: Sports Mole vs AiScore disagree on the same merged match.
    for c in clusters:
        comps = {s: comp for s, comp in c["_contrib"]}
        if "sportsmole" in comps and "aiscore" in comps:
            a = (comps["sportsmole"] or "").strip().lower()
            b = (comps["aiscore"] or "").strip().lower()
            if a and b and a != b and team_sim(a, b) < 80:
                c["discrepancy"] = True
                c["competition_variants"] = sorted({cp for _, cp in c["_contrib"] if cp})

    records = [_public(c) for c in clusters]
    records.sort(key=lambda x: (x["kickoff_utc"], x["competition"], x["home"]))

    feed = []
    for c in clusters:
        label = map_league(c["country"], c["competition"])
        if not label:
            continue
        feed.append({
            "league": label, "date": pd.Timestamp(c["_local_date"]), "time": c["_time"],
            "home": alias_normalize(c["home"]), "away": alias_normalize(c["away"]),
            "odds_h": None, "odds_d": None, "odds_a": None,
            "max_h": None, "max_d": None, "max_a": None,
            "odds_over25": None, "odds_under25": None,
            "src": f"live:{c['source']}",
        })

    if write:
        _write_outputs(records)
    return records, feed


def _write_outputs(records):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "all_fixtures.json"), "w") as f:
        json.dump({"generated_at": str(pd.Timestamp.now()),
                   "n_fixtures": len(records), "fixtures": records},
                  f, indent=1, ensure_ascii=False)
    with open(os.path.join(OUT, "all_fixtures.md"), "w") as f:
        f.write(to_markdown(records))


def to_markdown(records):
    """Render the comprehensive list as the user's requested table."""
    hdr = ("| Competition | Country | Home | Away | Kickoff (Tehran) | Kickoff (UTC) "
           "| Status | Source | URL |\n"
           "|---|---|---|---|---|---|---|---|---|\n")
    rows = []
    for r in records:
        flag = " ⚠️" if r.get("discrepancy") else ""
        also = ("+" + ",".join(r["also_sources"])) if r.get("also_sources") else ""
        cell = lambda s: str(s or "").replace("|", "/").replace("\n", " ")
        rows.append("| {comp}{flag} | {country} | {home} | {away} | {kl} | {ku} | "
                    "{st} | {src}{also} | {url} |".format(
                        comp=cell(r.get("competition")), flag=flag,
                        country=cell(r.get("country")), home=cell(r.get("home")),
                        away=cell(r.get("away")),
                        kl=cell(r.get("kickoff_local")), ku=cell(r.get("kickoff_utc")),
                        st=cell(r.get("status")), src=cell(r.get("source")), also=also,
                        url=cell(r.get("url"))))
    return f"# All upcoming fixtures ({len(records)})\n\n" + hdr + "\n".join(rows) + "\n"


def load_live_fixtures(days_ahead=2):
    """Public entry for fixtures_merge: the output-B feed rows (14-col dicts)."""
    _, feed = collect_all(days_ahead=days_ahead, write=True)
    return feed


if __name__ == "__main__":
    recs, feed = collect_all(days_ahead=2, write=True)
    print(f"comprehensive fixtures: {len(recs)}  ->  output/all_fixtures.json")
    print(f"model-mappable feed rows: {len(feed)}")
    if recs:
        from collections import Counter
        by_src = Counter(r["source"] for r in recs)
        print("by source:", dict(by_src))
        by_country = Counter(r["country"] for r in recs)
        print("top countries:", dict(sorted(by_country.items(), key=lambda kv: -kv[1])[:12]))
        print("\nsample:")
        for r in recs[:8]:
            print(f"  {r['kickoff_local'][:16]}  {r['competition']:<28} "
                  f"{r['home']} vs {r['away']}  [{r['source']}]")
    if feed:
        from collections import Counter
        print("\nfeed leagues:", dict(Counter(x["league"] for x in feed)))
