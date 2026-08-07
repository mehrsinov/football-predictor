# -*- coding: utf-8 -*-
"""Secure Telegram MTProto login wizard (Telethon).

Credentials come from env (injected via RunWithCredentials):
    TG_API_ID, TG_API_HASH  — from my.telegram.org (free)
    TG_PHONE                — phone in intl format e.g. +989121234567
    TG_2FA_PASSWORD         — optional, only if the account has cloud password

Modes:
    python3 tg_login.py request            -> sends login code to the user's Telegram app
    python3 tg_login.py confirm "1 2 3 4 5" -> completes login (code may contain spaces)
    python3 tg_login.py status             -> shows whether session is authorized

Session persistence:
    - primary: tg.session file next to the scripts (survives in this thread's container)
    - backup:  StringSession written to ../output/tg_session_string.txt — the user
      should copy it into the skill credential TG_SESSION, then delete the file.
      The session string is NEVER printed to stdout.
"""
import json
import os
import sys

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
SESSION_PATH = os.path.join(HERE, "tg")          # -> tg.session
STATE_PATH = os.path.join(OUT, "tg_login_state.json")
STRING_PATH = os.path.join(OUT, "tg_session_string.txt")


def env(k, required=True):
    v = os.environ.get(k, "").strip()
    if required and not v:
        print(f"ERROR: credential {k} is missing. آن را در فیلد امن اسکیل وارد کن.")
        sys.exit(2)
    return v


def get_client():
    api_id = int(env("TG_API_ID"))
    api_hash = env("TG_API_HASH")
    # prefer existing file session; fall back to TG_SESSION string credential
    if not os.path.exists(SESSION_PATH + ".session") and os.environ.get("TG_SESSION", "").strip():
        return TelegramClient(StringSession(os.environ["TG_SESSION"].strip()), api_id, api_hash)
    return TelegramClient(SESSION_PATH, api_id, api_hash)


def mode_request():
    phone = env("TG_PHONE")
    client = get_client()
    client.connect()
    if client.is_user_authorized():
        me = client.get_me()
        print(f"ALREADY_AUTHORIZED as {me.first_name} (id={me.id}) — نیازی به لاگین مجدد نیست.")
        return
    sent = client.send_code_request(phone)
    os.makedirs(OUT, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump({"phone": phone, "phone_code_hash": sent.phone_code_hash}, f)
    print("CODE_SENT: کد ورود به اپ تلگرامت ارسال شد (پیام از حساب رسمی Telegram).")
    print("کد را با فاصله بین رقم‌ها بفرست، مثل: 1 2 3 4 5")
    client.disconnect()


def mode_confirm(code_raw):
    code = "".join(ch for ch in code_raw if ch.isdigit())
    if not code:
        print("ERROR: کدی پیدا نشد.")
        sys.exit(2)
    if not os.path.exists(STATE_PATH):
        print("ERROR: اول mode=request را اجرا کن.")
        sys.exit(2)
    with open(STATE_PATH) as f:
        state = json.load(f)
    client = get_client()
    client.connect()
    try:
        client.sign_in(state["phone"], code, phone_code_hash=state["phone_code_hash"])
    except SessionPasswordNeededError:
        pw = os.environ.get("TG_2FA_PASSWORD", "").strip()
        if not pw:
            print("NEED_2FA: اکانت رمز ابری دارد. TG_2FA_PASSWORD را در credential های اسکیل وارد کن و دوباره confirm را اجرا کن.")
            sys.exit(3)
        client.sign_in(password=pw)
    me = client.get_me()
    # export string session to file only (never stdout)
    s = StringSession.save(client.session)
    os.makedirs(OUT, exist_ok=True)
    with open(STRING_PATH, "w") as f:
        f.write(s)
    os.remove(STATE_PATH)
    print(f"AUTHORIZED as {me.first_name} (id={me.id}) ✅")
    print(f"سشن در فایل tg.session ذخیره شد + نسخه پشتیبان متنی در output/tg_session_string.txt")
    print("توصیه: متن آن فایل را در credential TG_SESSION اسکیل ذخیره کن و سپس فایل را پاک کن.")
    client.disconnect()


def mode_status():
    client = get_client()
    client.connect()
    if client.is_user_authorized():
        me = client.get_me()
        print(f"AUTHORIZED as {me.first_name} (id={me.id})")
    else:
        print("NOT_AUTHORIZED")
    client.disconnect()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "request":
        mode_request()
    elif mode == "confirm":
        mode_confirm(" ".join(sys.argv[2:]))
    else:
        mode_status()
