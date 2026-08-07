# -*- coding: utf-8 -*-
"""Deliver the daily Persian report to Telegram and/or Gmail.

Telegram (free): create a bot via @BotFather -> TG_BOT_TOKEN; get your chat id
    (message the bot then read getUpdates, or use @userinfobot) -> TG_CHAT_ID.
Gmail (free): a Gmail App Password (16 chars) -> GMAIL_USER, GMAIL_APP_PASSWORD,
    GMAIL_TO (defaults to GMAIL_USER).

All credentials come from env (skill credentials). Nothing is printed.

CLI:
    python3 deliver.py telegram
    python3 deliver.py gmail
    python3 deliver.py all
"""
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
REPORT = os.path.join(OUT, "report_fa.md")


def read_report():
    if not os.path.exists(REPORT):
        print("ERROR: report_fa.md موجود نیست — اول run_daily.py را اجرا کن.")
        sys.exit(2)
    with open(REPORT, encoding="utf-8") as f:
        return f.read()


def send_telegram(text):
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat:
        print("SKIP telegram: TG_BOT_TOKEN یا TG_CHAT_ID تنظیم نشده.")
        return False
    # Telegram hard limit 4096 chars/msg -> chunk on blank lines
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > 3800:
            chunks.append(buf)
            buf = para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf:
        chunks.append(buf)
    ok = True
    for i, ch in enumerate(chunks):
        r = requests.post(url, json={"chat_id": chat, "text": ch,
                                     "disable_web_page_preview": True}, timeout=30)
        if r.status_code != 200:
            print(f"telegram chunk {i} failed: {r.status_code} {r.text[:150]}")
            ok = False
    if ok:
        print(f"TELEGRAM_OK: {len(chunks)} پیام ارسال شد.")
    return ok


def send_gmail(text):
    user = os.environ.get("GMAIL_USER", "").strip()
    # Google displays app passwords with spaces ("abcd efgh ...") — strip them
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")
    to = os.environ.get("GMAIL_TO", "").strip() or user
    if not user or not pw:
        print("SKIP gmail: GMAIL_USER یا GMAIL_APP_PASSWORD تنظیم نشده.")
        return False
    msg = MIMEMultipart("alternative")
    from datetime import date
    msg["Subject"] = f"⚽ گزارش پیش‌بینی فوتبال — {date.today().isoformat()}"
    msg["From"] = user
    msg["To"] = to
    html = "<div dir='rtl' style='font-family:Tahoma,Arial,sans-serif;white-space:pre-wrap;line-height:1.7'>" + \
           text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</div>"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
        print(f"GMAIL_OK: به {to} ارسال شد.")
        return True
    except Exception as ex:
        print(f"gmail failed: {ex}")
        return False


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    text = read_report()
    if mode in ("telegram", "all"):
        send_telegram(text)
    if mode in ("gmail", "all"):
        send_gmail(text)
