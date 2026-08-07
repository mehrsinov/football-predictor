# -*- coding: utf-8 -*-
"""Deterministic harvester for six public prediction sites.

Writes output/tips_sites.json in the canonical tip schema. Each site parser is
independent; failures are logged and skipped so one blocked site never kills
the run. Fetching uses curl_cffi Chrome TLS impersonation (Cloudflare-tolerant).
"""
import json
import os
import re
import sys
import time
from datetime import date

from bs4 import BeautifulSoup
from curl_cffi import requests as cr

OUT = os.path.join(os.path.dirname(__file__), "..", "output")
TODAY = date.today().isoformat()


def fetch(url, imp="chrome"):
    profiles = [imp] + [p for p in ("chrome110", "chrome124", "chrome", "safari17_0") if p != imp]
    last = None
    for p in profiles[:4]:
        try:
            r = cr.get(url, impersonate=p, timeout=30)
            if r.status_code == 200 and len(r.text) >= 5000:
                return BeautifulSoup(r.text, "lxml")
            last = f"{r.status_code} len={len(r.text)}"
        except Exception as ex:
            last = str(ex)
        time.sleep(1.5)
    raise RuntimeError(f"{url} -> {last}")


def tip(source, home, away, market, pick, prob=None, odds=None, league=None, url=None):
    return {"source": source, "source_type": "site", "lang": "en", "league": league,
            "home": home.strip(), "away": away.strip(), "match_date": TODAY,
            "market": market, "pick": pick, "prob": prob, "odds": odds, "url": url}


def cs_to_1x2(score):
    h, a = score.split("-")
    return "1" if int(h) > int(a) else ("2" if int(h) < int(a) else "X")


# ----------------------------------------------------------------- forebet
def forebet(tips):
    pages = [
        ("https://www.forebet.com/en/football-predictions", "1X2"),
        ("https://www.forebet.com/en/football-predictions/under-over-25-goals", "OU25"),
        ("https://www.forebet.com/en/football-predictions/predictions-both-to-score", "BTTS"),
    ]
    for url, mode in pages:
        soup = fetch(url)
        for row in soup.find_all("div", class_="rcnt"):
            try:
                home_el = row.find(class_="homeTeam") or row.find("span", class_="homeTeam")
                away_el = row.find(class_="awayTeam") or row.find("span", class_="awayTeam")
                if not home_el or not away_el:
                    continue
                home, away = home_el.get_text(strip=True), away_el.get_text(strip=True)
                fprc = row.find("div", class_="fprc")
                probs = [s.get_text(strip=True) for s in fprc.find_all("span")] if fprc else []
                pr = row.find("div", class_="predict_y") or row.find("div", class_="predict")
                pick_el = pr.find("span", class_=lambda x: x and "forepr" in str(x)) if pr else None
                pick = pick_el.get_text(strip=True) if pick_el else None
                link = row.find("a", class_="tnmscn")
                murl = ("https://www.forebet.com" + link["href"]) if link and link.has_attr("href") else url
                if mode == "1X2" and pick in ("1", "X", "2"):
                    prob = None
                    try:
                        prob = int(probs[{"1": 0, "X": 1, "2": 2}[pick]]) / 100.0
                    except Exception:
                        pass
                    tips.append(tip("forebet", home, away, "1X2", pick, prob, league=None, url=murl))
                    sc_el = pr.find("span", class_=lambda x: x and "scrmobpred" in str(x)) if pr else None
                    if sc_el:
                        sc = re.sub(r"[^\d\-]", "", sc_el.get_text(strip=True))
                        if re.fullmatch(r"\d+-\d+", sc):
                            tips.append(tip("forebet", home, away, "CS", sc, url=murl))
                elif mode == "OU25" and pick and pick.lower() in ("over", "under"):
                    prob = None
                    try:
                        prob = int(probs[1 if pick.lower() == "over" else 0]) / 100.0
                    except Exception:
                        pass
                    tips.append(tip("forebet", home, away, "OU25", pick.capitalize(), prob, url=murl))
                elif mode == "BTTS" and pick and pick.lower() in ("yes", "no"):
                    tips.append(tip("forebet", home, away, "BTTS", pick.capitalize(), url=murl))
            except Exception:
                continue


# ----------------------------------------------------------------- zulubet
def zulubet(tips):
    d = date.today()
    url = f"https://www.zulubet.com/tips-{d.day:02d}-{d.month:02d}-{d.year}.html"
    soup = fetch(url)
    table = soup.find("table", class_="content_table")
    if not table:
        raise RuntimeError("zulubet table missing")
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 10:
            continue
        m = cells[1].get_text(strip=True)
        if " - " not in m:
            continue
        home, away = [x.strip() for x in m.split(" - ", 1)]
        txt = cells[2].get_text(strip=True)
        pm = {k: (int(g.group(1)) / 100 if (g := re.search(rf"{k}: ?(\d+)%", txt)) else None)
              for k in ("1", "X", "2")}
        pick = cells[9].get_text(strip=True)
        if pick not in ("1", "X", "2", "1X", "X2", "12"):
            continue
        market = "DC" if pick in ("1X", "X2", "12") else "1X2"
        prob = pm.get(pick) if market == "1X2" else None
        tips.append(tip("zulubet", home, away, market, pick, prob, url=url))


# ---------------------------------------------------------------- predictz
def predictz(tips):
    soup = fetch("https://www.predictz.com/predictions/", "chrome110")
    league = None
    for el in soup.find_all(["h2", "div"]):
        if el.name == "h2":
            t = el.get_text(strip=True)
            if t.endswith("Tips"):
                league = t[:-5].strip()
            continue
        cls = el.get("class") or []
        if "pttr" not in cls or "ptcnt" not in cls:
            continue
        home_el, away_el = el.find("div", class_="ptmobh"), el.find("div", class_="ptmoba")
        pred_el = el.find("div", class_="ptpredboxsml") or el.find("div", class_="ptpredbox")
        if not (home_el and away_el and pred_el):
            continue
        home, away = home_el.get_text(strip=True), away_el.get_text(strip=True)
        pred = pred_el.get_text(strip=True)
        sc = re.search(r"(\d+)\s*[-:]\s*(\d+)", pred)
        score = f"{sc.group(1)}-{sc.group(2)}" if sc else None
        pl = pred.lower()
        pick = "1" if pl.startswith("home") else "2" if pl.startswith("away") else \
               "X" if pl.startswith("draw") else (cs_to_1x2(score) if score else None)
        u = "https://www.predictz.com/predictions/"
        if pick:
            tips.append(tip("predictz", home, away, "1X2", pick, league=league, url=u))
        if score:
            tips.append(tip("predictz", home, away, "CS", score, league=league, url=u))


# -------------------------------------------------------------- windrawwin
def windrawwin(tips):
    soup = fetch("https://www.windrawwin.com/predictions/today/", "chrome110")
    for row in soup.find_all("div", class_="wttr"):
        names = [d.get_text(strip=True) for d in row.find_all("div", class_="wtmoblnk")]
        if len(names) < 2:
            continue
        home, away = names[0], names[1]
        pred = row.find("div", class_="wtfullpred")
        if not pred:
            continue
        link = pred.find("a")
        murl = link["href"] if link and link.has_attr("href") else "https://www.windrawwin.com/predictions/today/"
        stake = pred.find("div", class_="predstake")
        sc_el = pred.find("span", class_="predscore")
        score = None
        if sc_el:
            m = re.match(r"(\d+)\s*[-:]\s*(\d+)", sc_el.get_text(strip=True))
            if m:
                score = f"{m.group(1)}-{m.group(2)}"
        st = (stake.get_text(strip=True) if stake else "").lower()
        pick = "1" if "home win" in st else "2" if "away win" in st else \
               "X" if "draw" in st else (cs_to_1x2(score) if score else None)
        parts = murl.split("/")
        league = parts[4].replace("-", " ").title() if len(parts) >= 5 and parts[4] else None
        if pick:
            tips.append(tip("windrawwin", home, away, "1X2", pick, league=league, url=murl))
        if score:
            tips.append(tip("windrawwin", home, away, "CS", score, league=league, url=murl))


# ----------------------------------------------------------------- vitibet
def vitibet(tips):
    soup = fetch("https://www.vitibet.com/")
    for card in soup.find_all("a", class_="viti-v6-card"):
        home_el = card.find("div", class_="viti-v6-team-home")
        away_el = card.find("div", class_="viti-v6-team-away")
        if not (home_el and away_el):
            continue
        home, away = home_el.get_text(strip=True), away_el.get_text(strip=True)
        score = None
        sc_el = card.find("span", class_="viti-v6-m-score")
        if sc_el:
            m = re.match(r"(\d+)\s*[:\-]\s*(\d+)", sc_el.get_text(strip=True))
            if m:
                score = f"{m.group(1)}-{m.group(2)}"
        pick = None
        badge = card.find("span", class_="viti-v6-badge")
        if badge:
            t = badge.get_text(strip=True)
            pick = t if t in ("1", "X", "2") else None
        if not pick and score:
            pick = cs_to_1x2(score)
        u = "https://www.vitibet.com/"
        if pick:
            tips.append(tip("vitibet", home, away, "1X2", pick, url=u))
        if score:
            tips.append(tip("vitibet", home, away, "CS", score, url=u))


# ---------------------------------------------------------------- statarea
def statarea(tips):
    soup = fetch("https://old.statarea.com/predictions")
    league = None
    for tbl in soup.find_all("table"):
        for row in tbl.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 1:
                t = cells[0].get_text(strip=True)
                if "," in t and not re.search(r"\d+%", t):
                    league = t
                continue
            if len(cells) < 5 or not re.match(r"^\d{1,2}:\d{2}$", cells[0].get_text(strip=True)):
                continue
            host = cells[1].get_text(strip=True).rstrip("-").strip()
            guest = cells[2].get_text(strip=True)
            pcts = re.findall(r"(\d+)%", row.get_text())
            if len(pcts) < 3 or not host or not guest:
                continue
            p1, px, p2 = (int(x) / 100 for x in pcts[:3])
            mx = max(p1, px, p2)
            if mx <= 0.45:
                continue
            pick, prob = ("1", p1) if p1 == mx else (("X", px) if px == mx else ("2", p2))
            tips.append(tip("statarea", host, guest, "1X2", pick, round(prob, 2),
                            league=league, url="https://old.statarea.com/predictions"))


def run():
    os.makedirs(OUT, exist_ok=True)
    tips, summary = [], {}
    for fn in (forebet, zulubet, predictz, windrawwin, vitibet, statarea):
        before = len(tips)
        try:
            fn(tips)
            summary[fn.__name__] = len(tips) - before
        except Exception as ex:
            summary[fn.__name__] = f"FAILED: {ex}"
            sys.stderr.write(f"{fn.__name__} failed: {ex}\n")
    # dedupe
    seen, out = set(), []
    for t in tips:
        k = (t["source"], t["home"], t["away"], t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    with open(os.path.join(OUT, "tips_sites.json"), "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(json.dumps({"total": len(out), "per_site": summary}))


if __name__ == "__main__":
    run()
