# -*- coding: utf-8 -*-
"""Settlement loop: snapshot today's picks, settle past picks against results,
and score every source by its realized hit-rate/ROI.

Commands:
    python3 settle_picks.py snapshot   # save today's curated picks to the ledger
    python3 settle_picks.py settle     # settle pending ledger entries vs results
    python3 settle_picks.py report     # print per-source score table

Files:
    data/picks_ledger.json       append-only ledger of every pick we ever made
    output/source_scores.json    per-source n / wins / roi / weight (for merge_rank)

A pick is settled as soon as its match appears in the historical feed with a
final score. Markets settled: 1X2, DC, OU15/25/35, BTTS, DNB. Flat-stake ROI;
legs without market odds are settled at fair odds (1/p).
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
OUT = os.path.join(ROOT, "output")
DATA = os.path.join(ROOT, "data")
LEDGER = os.path.join(DATA, "picks_ledger.json")
SCORES = os.path.join(OUT, "source_scores.json")

MIN_SAMPLE = 5  # below this many settled picks a source keeps neutral weight


def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ snapshot
def snapshot():
    """Append today's curated picks to the ledger (idempotent per match+market)."""
    picks = _load(os.path.join(OUT, "curated_picks.json"), {}).get("picks", [])
    if not picks:
        print("snapshot: no curated picks yet")
        return
    ledger = _load(LEDGER, {"entries": []})
    seen = {(e["date"], e["home"], e["away"], e["market"], e["pick"])
            for e in ledger["entries"]}
    added = 0
    for p in picks:
        # curated picks carry no date; stamp with today
        key = (datetime.now().strftime("%Y-%m-%d"), p["home"], p["away"],
               p["market"], p["pick"])
        if key in seen:
            continue
        ledger["entries"].append({
            "date": key[0],
            "home": p["home"], "away": p["away"], "league": p.get("league"),
            "market": p["market"], "pick": p["pick"],
            "p": p.get("p"), "odds": p.get("odds"),
            "n_sources": p.get("n_sources") or 0,
            "source_names": p.get("sources") or [],
            "status": "pending",
        })
        seen.add(key)
        added += 1
    _save(LEDGER, ledger)
    print(f"snapshot: +{added} picks (ledger total {len(ledger['entries'])})")


# ------------------------------------------------------------------- outcome
def outcome_of(market, pick, hg, ag):
    """Return 'won' / 'lost' / 'void' for a settled match."""
    tot = hg + ag
    if market == "1X2":
        res = "1" if hg > ag else ("X" if hg == ag else "2")
        return "won" if pick == res else "lost"
    if market == "DC":
        ok = {"1X": hg >= ag, "X2": hg <= ag, "12": hg != ag}.get(pick, False)
        return "won" if ok else "lost"
    if market in ("OU15", "OU25", "OU35"):
        line = {"OU15": 1.5, "OU25": 2.5, "OU35": 3.5}[market]
        over = tot > line
        return "won" if (pick == "Over") == over else "lost"
    if market == "BTTS":
        both = hg > 0 and ag > 0
        return "won" if (pick == "Yes") == both else "lost"
    if market == "DNB":
        if hg == ag:
            return "void"
        win = "1" if hg > ag else "2"
        return "won" if pick == win else "lost"
    return None  # market not settled (CS, AH, combos...)


# -------------------------------------------------------------------- settle
def settle():
    """Settle every pending ledger entry whose match now has a final score."""
    from data_loader import load_history
    from merge_rank import team_sim

    ledger = _load(LEDGER, {"entries": []})
    pending = [e for e in ledger["entries"] if e.get("status") == "pending"]
    if not pending:
        print("settle: nothing pending")
        return

    hist = load_history()
    # only recent history is relevant for pending picks
    cutoff = hist["date"].max() - pd.Timedelta(days=14)
    recent = hist[hist["date"] >= cutoff]

    settled_now = 0
    for e in pending:
        best, bs = None, 0
        for _, r in recent.iterrows():
            s = min(team_sim(e["home"], r["home"]), team_sim(e["away"], r["away"]))
            if s > bs:
                best, bs = r, s
        if best is None or bs < 85:
            continue  # match not played yet (or unmatchable)
        st = outcome_of(e["market"], e["pick"], int(best["hg"]), int(best["ag"]))
        if st is None:
            continue
        e["status"] = st
        e["result"] = f"{int(best['hg'])}-{int(best['ag'])}"
        settled_now += 1

    _save(LEDGER, ledger)
    score_sources()
    n_done = sum(1 for e in ledger["entries"] if e.get("status") != "pending")
    print(f"settle: {settled_now} new | ledger {n_done}/{len(ledger['entries'])} settled")


# ------------------------------------------------------------- source scores
def score_sources():
    """Per-source realized stats -> weights used by merge_rank consensus boost."""
    ledger = _load(LEDGER, {"entries": []})
    stats = {}
    roi_all, n_all = 0.0, 0
    for e in ledger["entries"]:
        if e.get("status") not in ("won", "lost", "void"):
            continue
        p, odds = e.get("p"), e.get("odds")
        if not p:
            continue
        stake_odds = float(odds) if odds else 1.0 / p
        if e["status"] == "won":
            pnl = stake_odds - 1
        elif e["status"] == "lost":
            pnl = -1
        else:
            pnl = 0.0
        roi_all += pnl
        n_all += 1
        for name in e.get("source_names") or []:
            s = stats.setdefault(str(name), {"n": 0, "wins": 0, "roi": 0.0})
            s["n"] += 1
            s["wins"] += e["status"] == "won"
            s["roi"] += pnl

    sources = {}
    for name, s in stats.items():
        roi = s["roi"] / s["n"] if s["n"] else 0.0
        # weight: neutral 1.0 until enough sample, then ROI-shifted, clamped
        w = 1.0 if s["n"] < MIN_SAMPLE else max(0.5, min(1.5, 1.0 + roi))
        sources[name] = {"n": s["n"], "wins": s["wins"],
                         "hit": round(s["wins"] / s["n"], 3) if s["n"] else None,
                         "roi": round(roi, 3), "weight": round(w, 2)}
    out = {"as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "n_settled_picks": n_all,
           "overall_roi": round(roi_all / n_all, 3) if n_all else None,
           "sources": sources}
    _save(SCORES, out)
    return out


# -------------------------------------------------------------------- report
def report():
    out = _load(SCORES, {})
    if not out.get("sources"):
        print("no settled picks yet — run a few daily cycles first")
        return
    print(f"پیک‌های تسویه‌شده: {out.get('n_settled_picks')} | ROI کل: {out.get('overall_roi')}")
    rows = sorted(out["sources"].items(), key=lambda kv: (-kv[1]["n"], -kv[1]["roi"]))
    for name, s in rows[:25]:
        print(f"  {name:<28} n={s['n']:<4} hit={s['hit']} roi={s['roi']:+.3f} w={s['weight']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "settle"
    {"snapshot": snapshot, "settle": settle, "report": report}.get(cmd, settle)()
