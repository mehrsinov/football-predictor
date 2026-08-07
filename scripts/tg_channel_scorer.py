# -*- coding: utf-8 -*-
"""Objective channel validator: grade the user's Telegram tip channels by the
real hit-rate of their PAST tips against historical results.

For each football-looking channel the user is in, read the last N days of
messages, extract dated tips, match each to a completed fixture in our history
(fuzzy team match), and settle it. Produces a per-channel scorecard:
    tips_found, settled, wins, hit_rate, avg_odds (if present), sample.

This is how we answer the user's "you should verify the channels yourself".

CLI:
    python3 tg_channel_scorer.py --days 60
Output: output/tg_channel_scores.json
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

from tg_tip_extract import extract_tips
from data_loader import load_history
from merge_rank import team_sim  # reuse the fuzzy matcher

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
SESSION_PATH = os.path.join(HERE, "tg")
import re as _re
FOOTBALL_HINT = _re.compile(
    r"(bet|tip|pronost|palpite|prognoz|прогноз|ставк|iddaa|توقع|پیش.?بینی|بت|شرط|فوتبال|"
    r"soccer|futbol|football|goal|prediction|كرة|كورة)", _re.IGNORECASE)


def get_client():
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    if not os.path.exists(SESSION_PATH + ".session") and os.environ.get("TG_SESSION", "").strip():
        return TelegramClient(StringSession(os.environ["TG_SESSION"].strip()), api_id, api_hash)
    return TelegramClient(SESSION_PATH, api_id, api_hash)


def settle(tip, hist_window):
    """Find the completed match for a tip and decide win/lose. Returns
    (settled_bool, win_bool) or (False, None) if no confident match."""
    cand = hist_window
    best, best_s = None, 0
    for _, r in cand.iterrows():
        s = min(team_sim(tip["home"], r["home"]), team_sim(tip["away"], r["away"]))
        if s > best_s:
            best, best_s = r, s
    if best is None or best_s < 80:
        return False, None
    hg, ag = int(best["hg"]), int(best["ag"])
    tot = hg + ag
    m, p = tip["market"], tip["pick"]
    res = "1" if hg > ag else ("2" if hg < ag else "X")
    if m == "1X2":
        return True, (p == res)
    if m == "DC":
        return True, (res in {"1X": {"1", "X"}, "X2": {"X", "2"}, "12": {"1", "2"}}[p])
    if m.startswith("OU"):
        line = float(m[2] + ".5")
        return True, ((tot > line) if p == "Over" else (tot < line))
    if m == "BTTS":
        yes = hg > 0 and ag > 0
        return True, (yes if p == "Yes" else (not yes))
    if m == "CS":
        return True, (p == f"{hg}-{ag}")
    return False, None


def run(days=60, per_channel=400, max_channels=60):
    client = get_client()
    client.connect()
    if not client.is_user_authorized():
        print("NOT_AUTHORIZED: اول tg_login.py را کامل کن.")
        sys.exit(3)

    hist = load_history()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scores = []
    for dialog in client.iter_dialogs():
        if len(scores) >= max_channels:
            break
        if not (dialog.is_channel or dialog.is_group):
            continue
        name = dialog.name or ""
        if not FOOTBALL_HINT.search(name):
            continue
        found, settled_n, wins, odds_list = [], 0, 0, []
        try:
            for msg in client.iter_messages(dialog.id, limit=per_channel):
                if not msg.date or msg.date < cutoff:
                    break
                if not msg.message:
                    continue
                md = msg.date.date().isoformat()
                for t in extract_tips(msg.message, source=name, match_date=md):
                    # settle against +/- 2 day window around post date
                    d0 = pd.Timestamp(msg.date.date()) - pd.Timedelta(days=1)
                    d1 = pd.Timestamp(msg.date.date()) + pd.Timedelta(days=2)
                    win_win = hist[(hist["date"] >= d0) & (hist["date"] <= d1)]
                    ok, win = settle(t, win_win)
                    found.append(t)
                    if ok:
                        settled_n += 1
                        wins += int(win)
                        if t.get("odds"):
                            odds_list.append(t["odds"])
        except Exception as ex:
            sys.stderr.write(f"skip {name}: {ex}\n")
            continue
        if found:
            scores.append({
                "channel": name,
                "tips_found": len(found),
                "settled": settled_n,
                "wins": wins,
                "hit_rate": round(wins / settled_n, 3) if settled_n else None,
                "avg_odds": round(sum(odds_list) / len(odds_list), 2) if odds_list else None,
                "implied_breakeven": round(1 / (sum(odds_list) / len(odds_list)), 3) if odds_list else None,
            })
        time.sleep(0.8)

    client.disconnect()
    # rank: enough settled sample first, then hit rate
    scores.sort(key=lambda s: (s["settled"] >= 10, s["hit_rate"] or 0), reverse=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tg_channel_scores.json"), "w") as f:
        json.dump({"days": days, "channels": scores}, f, ensure_ascii=False, indent=1)
    print(json.dumps({"channels_graded": len(scores),
                      "top": [{k: s[k] for k in ("channel", "settled", "hit_rate", "avg_odds")}
                              for s in scores[:12]]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    days = 60
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    run(days=days)
