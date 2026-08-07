# -*- coding: utf-8 -*-
"""Harvest tips from the user's joined Telegram channels/groups (MTProto).

Reads recent messages from dialogs that look like football/betting channels,
extracts concrete tips with tg_tip_extract, and writes them to
output/tips_telegram_personal.json.

Requires an authorized session (see tg_login.py). Read-only, rate-limited.

CLI:
    python3 tg_harvest.py                 # auto-detect football channels, last 24h
    python3 tg_harvest.py --hours 36
    python3 tg_harvest.py --all-dialogs   # scan every dialog, not just football-looking ones
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

from tg_tip_extract import extract_tips

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
SESSION_PATH = os.path.join(HERE, "tg")

FOOTBALL_HINT = re.compile(
    r"(bet|tip|pronost|palpite|prognoz|прогноз|ставк|iddaa|توقع|پیش.?بینی|بت|شرط|فوتبال|"
    r"soccer|futbol|football|goal|over|under|prediction|كرة|كورة)", re.IGNORECASE)


def get_client():
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    if not os.path.exists(SESSION_PATH + ".session") and os.environ.get("TG_SESSION", "").strip():
        return TelegramClient(StringSession(os.environ["TG_SESSION"].strip()), api_id, api_hash)
    return TelegramClient(SESSION_PATH, api_id, api_hash)


def run(hours=24, all_dialogs=False, max_channels=40, per_channel=60):
    client = get_client()
    client.connect()
    if not client.is_user_authorized():
        print("NOT_AUTHORIZED: اول tg_login.py را کامل کن.")
        sys.exit(3)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    tips, scanned, channels_used = [], 0, []
    for dialog in client.iter_dialogs():
        if scanned >= max_channels:
            break
        if not (dialog.is_channel or dialog.is_group):
            continue
        name = dialog.name or ""
        if not all_dialogs and not FOOTBALL_HINT.search(name):
            continue
        scanned += 1
        got = 0
        try:
            for msg in client.iter_messages(dialog.id, limit=per_channel):
                if not msg.date or msg.date < cutoff:
                    break
                if not msg.message:
                    continue
                found = extract_tips(msg.message, source=f"tg:{name}",
                                     source_type="telegram_personal",
                                     url=f"https://t.me/c/{abs(dialog.id)}/{msg.id}",
                                     match_date=None)
                tips.extend(found)
                got += len(found)
        except Exception as ex:
            sys.stderr.write(f"skip {name}: {ex}\n")
            continue
        if got:
            channels_used.append({"channel": name, "tips": got})
        time.sleep(0.8)  # gentle rate limit

    client.disconnect()
    # dedupe globally
    seen, out = set(), []
    for t in tips:
        k = (t["source"], t["home"].lower(), t["away"].lower(), t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tips_telegram_personal.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps({"channels_scanned": scanned, "channels_with_tips": len(channels_used),
                      "total_tips": len(out), "top": channels_used[:15]}, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    hours = 24
    if "--hours" in args:
        hours = int(args[args.index("--hours") + 1])
    run(hours=hours, all_dialogs="--all-dialogs" in args)
