# -*- coding: utf-8 -*-
"""Hugging Face Space (Gradio) launcher for the football Telegram bot.

Runs the Telegram long-poll bot in a background thread and shows a tiny status
page (its HTTP endpoint is what keep-alive pings hit). On first boot it
downloads the latest analyzer code (same public zip the GitHub workflow uses),
installs deps, then starts bot.py's polling loop.

Secrets (Settings -> Variables and secrets):
    TG_BOT_TOKEN   (required)
    ADMIN_CHAT_ID  (optional - restrict the bot to your own chat)
    AI_PROVIDER / AI_API_KEY / AI_MODEL   (optional - read bet-slip photos)
"""
import glob
import os
import subprocess
import sys
import threading
import time
import zipfile

import requests

CODE_URL = "https://pub.hyperagent.com/api/published/pbf01KYAAF89P_NVQAQCXZDVHM48HW/fpbot-v7-final.zip"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(APP_DIR, "scripts")
STATUS = {"state": "booting", "since": time.strftime("%Y-%m-%d %H:%M"), "detail": ""}


def _run(cmd):
    return subprocess.run(cmd, shell=True)


def _local_zip():
    for pat in ("code.zip", "football-predictor*.zip"):
        hits = glob.glob(os.path.join(APP_DIR, pat))
        if hits:
            return hits[0]
    return None


def bootstrap():
    """Get analyzer code (local zip first, else download w/ browser UA) + install deps."""
    if os.path.exists(os.path.join(SCRIPTS, "bot.py")):
        return
    zp = _local_zip()
    if not zp:
        zp = os.path.join(APP_DIR, "code_dl.zip")
        last = None
        for attempt in range(4):
            try:
                r = requests.get(CODE_URL, headers=UA, timeout=60)
                if r.status_code == 200 and len(r.content) > 50000:
                    with open(zp, "wb") as f:
                        f.write(r.content)
                    last = None
                    break
                last = f"HTTP {r.status_code} ({len(r.content)}B)"
            except Exception as ex:
                last = str(ex)[:100]
            time.sleep(5)
        if last:
            raise RuntimeError(f"download failed: {last}")
    with zipfile.ZipFile(zp) as z:
        z.extractall(os.path.join(APP_DIR, "_code"))
    _run(f"cp -r '{APP_DIR}/_code/scripts' '{APP_DIR}/' && cp '{APP_DIR}/_code/requirements.txt' '{APP_DIR}/req_code.txt'")
    _run(f"pip install --no-cache-dir -r '{APP_DIR}/req_code.txt'")
    if not os.path.exists(os.path.join(SCRIPTS, "bot.py")):
        raise RuntimeError("بسته کد ناقص است (bot.py پیدا نشد)")


def run_bot():
    try:
        STATUS["state"] = "installing"
        bootstrap()
        STATUS["state"] = "starting bot"
        sys.path.insert(0, SCRIPTS)
        os.chdir(SCRIPTS)
        import bot
        STATUS["state"] = "running"
        bot.main()   # blocks in its polling loop
    except Exception as ex:
        STATUS["state"] = "error"
        STATUS["detail"] = str(ex)[:300]
        sys.stderr.write("BOT ERROR: " + str(ex) + "\n")


threading.Thread(target=run_bot, daemon=True).start()

# --- ZeroGPU compatibility -------------------------------------------------
# HF's ZeroGPU hardware refuses to start unless the app registers at least one
# @spaces.GPU function ("No @spaces.GPU function detected during startup").
# The bot itself is CPU-only, so we register a harmless no-op just to satisfy
# that startup check. Guarded so it's a no-op on plain CPU Spaces too.
try:
    import spaces

    @spaces.GPU(duration=1)
    def _zerogpu_warmup():
        return True
except Exception:
    pass
# ---------------------------------------------------------------------------

import gradio as gr


def status_md():
    s = STATUS["state"]
    emoji = {"running": "🟢", "error": "🔴"}.get(s, "🟡")
    extra = f"\n\n**جزئیات:** `{STATUS['detail']}`" if STATUS["detail"] else ""
    tip = ("\n\nربات زنده است ✅ در تلگرام `/start` بده." if s == "running"
           else "\n\nچند دقیقه صبر کن و صفحه را رفرش کن.")
    return (f"## ⚽ ربات پیش‌بین فوتبال\n\n**وضعیت:** {emoji} {s}{extra}{tip}\n\n"
            f"از: {STATUS['since']}\n\n*این صفحه فقط برای بیدار نگه‌داشتن سرویس است.*")


with gr.Blocks(title="Football Predictor Bot") as demo:
    gr.Markdown("# ⚽ Football Predictor — Telegram Bot")
    box = gr.Markdown(status_md())
    demo.load(status_md, outputs=box)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, ssr_mode=False)
