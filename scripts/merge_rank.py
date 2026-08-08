"""Merge model predictions with harvested tips, score and rank every option.

Inputs:  output/model_predictions.json, output/tips_*.json
Output:  output/ranked_options.json

Every (match, market, pick) becomes one OPTION scored by:
  - blended probability (model + de-vigged market)  [main ranking key]
  - tipster consensus (how many independent sources back it)
  - EV at best available odds, with anti-longshot guardrails

Options on matches the model can't cover keep tipster consensus + any
site-published probabilities, and are ranked in a separate 'tips_only' pool.
"""
import glob
import json
import os
import re
import unicodedata

import numpy as np
from rapidfuzz import fuzz

OUT = os.path.join(os.path.dirname(__file__), "..", "output")

# guardrails for calling something a "value" flag
VALUE_MIN_PROB = 0.28
VALUE_MAX_ODDS = 6.0
VALUE_MAX_DISAGREE = 0.20
VALUE_MIN_EV = 0.05

STOPWORDS = {"fc", "cf", "sc", "ac", "if", "fk", "bk", "sk", "cd", "ca", "club", "de", "the",
             "utd", "united", "city", "town", "athletic", "atletico", "sport", "sporting"}


def norm_team(name):
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    toks = [t for t in s.split() if t]
    return " ".join(toks)


def _stem(tok):
    return tok[:-1] if tok.endswith("s") and len(tok) > 3 else tok


def team_sim(a, b):
    na, nb = norm_team(a), norm_team(b)
    if not na or not nb:
        return 0
    base = max(fuzz.token_set_ratio(na, nb), fuzz.partial_ratio(na, nb))
    # bonus if distinctive tokens overlap (with light stemming + per-token fuzz)
    ta = {_stem(t) for t in na.split() if t not in STOPWORDS and len(t) > 2}
    tb = {_stem(t) for t in nb.split() if t not in STOPWORDS and len(t) > 2}
    if ta and tb:
        if ta & tb:
            base = max(base, 88)
        else:
            for x in ta:
                for y in tb:
                    if fuzz.ratio(x, y) >= 86:
                        base = max(base, 85)
    return base


MARKET_KEYS = {"1X2", "DC", "OU15", "OU25", "OU35", "BTTS", "CS", "AH"}
PICK_MAP = {
    "1": ("1X2", "1"), "x": ("1X2", "X"), "2": ("1X2", "2"),
    "home": ("1X2", "1"), "draw": ("1X2", "X"), "away": ("1X2", "2"),
    "home win": ("1X2", "1"), "away win": ("1X2", "2"),
    "1x": ("DC", "1X"), "x2": ("DC", "X2"), "12": ("DC", "12"),
    "over": ("OU25", "Over"), "under": ("OU25", "Under"),
    "yes": ("BTTS", "Yes"), "no": ("BTTS", "No"), "gg": ("BTTS", "Yes"), "ng": ("BTTS", "No"),
}
# Extended markets we recognize but do NOT model. They must pass through intact
# (never be re-mapped onto a core market by PICK_MAP) so they surface in the
# merged tips for the assistant/raw layers without a fabricated probability.
_EXT_PREFIXES = ("CORN", "CARD", "OFF", "SHOT", "PLAYER", "HT_", "OU_", "TEAM")
_EXT_EXACT = {"DNB", "EH", "HTFT", "COMBO"}


def canon_tip(tip):
    market = str(tip.get("market") or "").upper().replace("O/U", "OU").replace(".", "")
    pick = str(tip.get("pick") or "").strip()

    # 1) Recognized-but-unmodelled extended markets → pass through (need a pick).
    if market in _EXT_EXACT or market.startswith(_EXT_PREFIXES):
        return None if not pick else {**tip, "market": market, "pick": pick[:60]}

    # 2) Any Over/Under total (incl. lines we don't model, e.g. OU05/OU45) → keep
    #    the exact market code; only the modelled lines (OU15/25/35) get scored.
    if re.fullmatch(r"OU\d{2}", market):
        pick = pick.capitalize()
        if pick not in {"Over", "Under"}:
            return None
        return {**tip, "market": market, "pick": pick}

    # 3) Core markets, or infer a core market from an unambiguous pick token.
    if market not in MARKET_KEYS:
        mp = PICK_MAP.get(pick.lower())
        if mp:
            market, pick = mp
        elif re.fullmatch(r"\d+\s*-\s*\d+", pick):
            market = "CS"
        elif market and pick:
            # A specific custom market we don't recognize — keep it, unranked,
            # rather than dropping the user's tip on the floor.
            return {**tip, "market": market, "pick": pick[:60]}
        else:
            return None
    if market == "1X2":
        pick = {"HOME": "1", "AWAY": "2", "DRAW": "X"}.get(pick.upper(), pick.upper())
        if pick not in {"1", "X", "2"}:
            return None
    if market == "DC":
        pick = pick.upper()
        if pick not in {"1X", "X2", "12"}:
            return None
    if market.startswith("OU"):
        pick = pick.capitalize()
        if pick not in {"Over", "Under"}:
            return None
    if market == "BTTS":
        pick = pick.capitalize()
        if pick not in {"Yes", "No"}:
            return None
    if market == "CS":
        pick = re.sub(r"\s", "", pick)
        if not re.fullmatch(r"\d+-\d+", pick):
            return None
    t = dict(tip)
    t["market"], t["pick"] = market, pick
    return t


def load_source_scores():
    """Realized per-source weights from the settlement loop (1.0 = neutral)."""
    p = os.path.join(OUT, "source_scores.json")
    try:
        with open(p) as f:
            return json.load(f).get("sources") or {}
    except Exception:
        return {}


def load_tips():
    tips = []
    for path in glob.glob(os.path.join(OUT, "tips_*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            for tip in data if isinstance(data, list) else []:
                c = canon_tip(tip)
                if c and c.get("home") and c.get("away"):
                    tips.append(c)
        except Exception as ex:
            print(f"WARN could not load {path}: {ex}")
    return tips


def match_fixture(tip, fixtures):
    best, best_score = None, 0
    for fx in fixtures:
        s = min(team_sim(tip["home"], fx["home"]), team_sim(tip["away"], fx["away"]))
        if s > best_score:
            best, best_score = fx, s
    return (best, best_score) if best_score >= 80 else (None, best_score)


def model_prob_for(fx, market, pick):
    m = fx["model"]
    bl = fx.get("blend")
    if market == "1X2":
        model_p = {"1": m["p_home"], "X": m["p_draw"], "2": m["p_away"]}[pick]
        blend_p = {"1": bl["p_home"], "X": bl["p_draw"], "2": bl["p_away"]}[pick] if bl else model_p
        return model_p, blend_p
    if market == "DC":
        model_p = {"1X": m["p_1x"], "X2": m["p_x2"], "12": m["p_12"]}[pick]
        if bl:
            blend_p = {"1X": bl["p_home"] + bl["p_draw"], "X2": bl["p_draw"] + bl["p_away"],
                       "12": bl["p_home"] + bl["p_away"]}[pick]
        else:
            blend_p = model_p
        return model_p, blend_p
    key = {"OU15": ("p_over15",), "OU25": ("p_over25",), "OU35": ("p_over35",)}.get(market)
    if key:
        p_over = m[key[0]]
        p = p_over if pick == "Over" else 1 - p_over
        return p, p
    if market == "BTTS":
        p = m["p_btts_yes"] if pick == "Yes" else m["p_btts_no"]
        return p, p
    if market == "CS":
        for s in m["top_scores"]:
            if s["score"] == pick:
                return s["p"], s["p"]
        return 0.04, 0.04  # scores outside top-3: small default
    return None, None


def _clean_odds(v):
    try:
        f = float(v)
        return f if f > 1.0 and np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def best_odds_for(fx, market, pick):
    o = fx["odds"]
    if market == "1X2":
        raw = {"1": o.get("max_h") or o.get("h"), "X": o.get("max_d") or o.get("d"),
               "2": o.get("max_a") or o.get("a")}.get(pick)
        return _clean_odds(raw)
    if market == "OU25":
        return _clean_odds(o.get("over25") if pick == "Over" else o.get("under25"))
    return None


def run():
    with open(os.path.join(OUT, "model_predictions.json")) as f:
        preds = json.load(f)
    fixtures = preds["fixtures"]
    tips = load_tips()
    src_scores = load_source_scores()

    options = {}   # (fx_idx, market, pick) -> option
    unmatched = {}

    # 1) seed options from every tip
    n_matched = 0
    for tip in tips:
        fx, score = match_fixture(tip, fixtures)
        if fx is None:
            key = (norm_team(tip["home"]), norm_team(tip["away"]), tip["market"], tip["pick"])
            u = unmatched.setdefault(key, {
                "home": tip["home"], "away": tip["away"], "league": tip.get("league"),
                "market": tip["market"], "pick": tip["pick"], "sources": [], "site_probs": []})
            u["sources"].append({"name": tip.get("source"), "type": tip.get("source_type"),
                                 "lang": tip.get("lang")})
            if tip.get("prob"):
                u["site_probs"].append(tip["prob"])
            continue
        n_matched += 1
        idx = fixtures.index(fx)
        key = (idx, tip["market"], tip["pick"])
        opt = options.setdefault(key, {"fixture": fx, "market": tip["market"], "pick": tip["pick"],
                                       "sources": [], "site_probs": [], "notes": []})
        opt["sources"].append({"name": tip.get("source"), "type": tip.get("source_type"),
                               "lang": tip.get("lang")})
        if tip.get("prob"):
            opt["site_probs"].append(tip["prob"])
        # keep Persian/YouTube reasoning notes for the assistant layer
        if tip.get("note") and tip.get("source_type") in ("youtube", "telegram", "telegram_personal", "forum"):
            if len(opt["notes"]) < 3:
                opt["notes"].append({"src": str(tip.get("source"))[:40], "note": str(tip["note"])[:120]})

    # 2) add the model's own best picks for every fixture (even without tips)
    for idx, fx in enumerate(fixtures):
        cands = [("1X2", p, 0.55) for p in ("1", "X", "2")] + \
                [("OU15", "Over", 0.70), ("OU15", "Under", 0.62),
                 ("OU25", "Over", 0.58), ("OU25", "Under", 0.58),
                 ("OU35", "Over", 0.60), ("OU35", "Under", 0.70),
                 ("BTTS", "Yes", 0.58), ("BTTS", "No", 0.58),
                 ("DC", "1X", 0.78), ("DC", "X2", 0.78)]
        for market, pick, thr in cands:
            _, bp = model_prob_for(fx, market, pick)
            if bp and bp >= thr:
                options.setdefault((idx, market, pick),
                                   {"fixture": fx, "market": market, "pick": pick,
                                    "sources": [], "site_probs": [], "notes": []})

    # 3) score every option
    scored = []
    for (idx, market, pick), opt in options.items():
        fx = opt["fixture"]
        model_p, blend_p = model_prob_for(fx, market, pick)
        if blend_p is None:
            continue
        n_src = len({(s["name"], s["type"]) for s in opt["sources"]})
        odds = best_odds_for(fx, market, pick)
        ev = None
        value_flag = False
        if odds and odds > 1:
            ev = blend_p * odds - 1
            mkt = fx.get("market_devig")
            disagree = 0.0
            if market == "1X2" and mkt:
                disagree = abs(model_p - {"1": mkt["h"], "X": mkt["d"], "2": mkt["a"]}[pick])
            value_flag = (ev >= VALUE_MIN_EV and blend_p >= VALUE_MIN_PROB
                          and odds <= VALUE_MAX_ODDS and disagree <= VALUE_MAX_DISAGREE
                          and fx["model"]["known_teams"])
        # consensus weighted by each source's realized hit-rate/ROI (settlement
        # loop); unknown sources count 1.0
        w_src = sum(float((src_scores.get(str(s["name"])) or {}).get("weight", 1.0))
                    for s in {(s["name"], s["type"]): s for s in opt["sources"]}.values())
        consensus_boost = min(w_src, 6.0) * 0.012
        rank_score = blend_p + consensus_boost + (0.02 if value_flag else 0)
        if not fx["model"]["known_teams"]:
            rank_score -= 0.10
        scored.append({
            "league": fx["league"], "date": fx["date"], "time": fx["time"],
            "home": fx["home"], "away": fx["away"],
            "market": market, "pick": pick,
            "model_p": round(model_p, 3), "blend_p": round(blend_p, 3),
            "site_probs_avg": round(float(np.mean(opt["site_probs"])), 3) if opt["site_probs"] else None,
            "n_sources": n_src,
            "source_weight": round(w_src, 2),
            "source_names": sorted({str(s["name"]) for s in opt["sources"]})[:8],
            "odds": odds, "ev": round(ev, 3) if ev is not None else None,
            "value_flag": value_flag,
            "notes": (opt.get("notes") or [])[:2],
            "known_teams": fx["model"]["known_teams"],
            "xg": f"{fx['model']['xg_home']}-{fx['model']['xg_away']}",
            "top_scores": fx["model"]["top_scores"],
            "injuries": fx.get("injuries") or {},
            "src": fx.get("src", "football-data"),
            "rank_score": round(rank_score, 4),
        })
    scored.sort(key=lambda o: -o["rank_score"])

    # 4) tips-only pool (matches outside model coverage), ranked by consensus
    tips_only = []
    for u in unmatched.values():
        n_src = len({(s["name"], s["type"]) for s in u["sources"]})
        tips_only.append({**{k: u[k] for k in ("home", "away", "league", "market", "pick")},
                          "n_sources": n_src,
                          "source_names": sorted({str(s["name"]) for s in u["sources"]})[:8],
                          "site_probs_avg": round(float(np.mean(u["site_probs"])), 3) if u["site_probs"] else None})
    tips_only.sort(key=lambda o: (-o["n_sources"], -(o["site_probs_avg"] or 0)))

    # human-readable source summary (by file/source_type)
    stype_counts = {}
    for t in tips:
        st = t.get("source_type", "site")
        stype_counts[st] = stype_counts.get(st, 0) + 1
    STYPE_FA = {"site": "سایت‌های پیش‌بینی", "youtube": "یوتیوب", "telegram": "تلگرام عمومی",
                "telegram_personal": "تلگرام شخصی", "forum": "انجمن‌ها"}
    sources_used = "، ".join(f"{STYPE_FA.get(k, k)} ({v})" for k, v in
                             sorted(stype_counts.items(), key=lambda kv: -kv[1]))
    tgp = stype_counts.get("telegram_personal")

    result = {
        "n_tips_total": len(tips), "n_tips_matched": n_matched,
        "n_options_model": len(scored), "n_options_tips_only": len(tips_only),
        "sources_used": sources_used,
        "telegram_personal": (f"{tgp} تیپ از کانال‌های عضوشده‌ات" if tgp else None),
        "options": scored, "tips_only": tips_only[:60],
    }
    with open(os.path.join(OUT, "ranked_options.json"), "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(f"tips={len(tips)} matched={n_matched} | model-covered options={len(scored)} | tips-only={len(tips_only)}")


if __name__ == "__main__":
    run()
