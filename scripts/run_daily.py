"""Daily pipeline orchestrator (v2: xG + FotMob + injuries + more sources + delivery).

Usage:
    python3 run_daily.py                 # full run
    python3 run_daily.py --fast          # skip history refresh (fixtures only)
    python3 run_daily.py --no-harvest    # skip web harvesting (use existing tip files)
    python3 run_daily.py --deliver       # also send to Telegram/Gmail at the end
    python3 run_daily.py --with-telegram-personal   # also read user's joined channels

Bootstraps all historical data automatically on first run in a fresh container.
Each stage is fault-isolated: one failure never aborts the report.
"""
import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date

try:
    from curl_cffi import requests as _cffi
except Exception:  # pragma: no cover - curl_cffi is in requirements.txt
    _cffi = None

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "data", "raw")
PROC = os.path.join(HERE, "..", "data", "processed")
OUT = os.path.join(HERE, "..", "output")
LOG_DIR = os.path.join(HERE, "..", "logs")

# --------------------------------------------------------------------------- #
# Logging: timestamped, levelled, to both stdout (visible in GitHub Actions)
# and a rotating-per-run log file under ../logs so failures leave a trace.
# --------------------------------------------------------------------------- #
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "run_daily.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("run_daily")

# Full browser-like headers so Cloudflare / anti-bot layers don't 403 us.
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.football-data.co.uk/",
    "Connection": "keep-alive",
}

EXTRA = ["ARG", "AUT", "BRA", "CHN", "DNK", "FIN", "IRL", "JPN", "MEX", "NOR",
         "POL", "ROU", "RUS", "SWE", "SWZ", "USA"]
DIVS = ["E0", "E1", "E2", "E3", "SC0", "SC1", "D1", "D2", "I1", "I2",
        "SP1", "SP2", "F1", "F2", "N1", "B1", "P1", "T1", "G1"]
UNDERSTAT = ["EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "RFPL"]


def current_seasons():
    t = date.today()
    y = t.year % 100
    if t.month >= 7:
        return [f"{y}{y+1:02d}", f"{y-1:02d}{y}"]
    return [f"{y-1:02d}{y}", f"{y-2:02d}{y-1:02d}"]


def dl(url, dest, headers=None, retries=3, timeout=40):
    """Download *url* to *dest* with browser headers, retries and validation.

    Returns True only when the file was freshly downloaded, is non-trivial in
    size, and looks like real data (CSV/JSON/etc., not an HTML error page).

    Retry/backoff: 3 attempts, waiting 5s → 15s between failures. A 403/429
    (anti-bot) response is retried with a fresh browser impersonation profile,
    which usually defeats Cloudflare; other errors retry as-is.

    Critical anti-staleness rule: when the download ultimately fails we DELETE
    *dest*. A stale file left in place silently poisons the whole pipeline with
    last week's data (the bug this replaces) — better to fail loudly in the
    Actions log than to report old data as fresh.
    """
    if headers is None:
        headers = _HTTP_HEADERS

    profiles = ["chrome124", "chrome120", "chrome110", "chrome", "safari17_0"]

    for attempt in range(1, retries + 1):
        try:
            if _cffi is not None:
                prof = profiles[(attempt - 1) % len(profiles)]
                r = _cffi.get(url, impersonate=prof, headers=headers,
                              timeout=timeout, allow_redirects=True)
                status = r.status_code
                body = r.content
            else:  # fallback: plain curl with the same headers (no TLS impersonation)
                curl_cmd = ["curl", "-s", "-L", "--max-time", str(timeout),
                            "-A", headers.get("User-Agent", ""),
                            "-e", headers.get("Referer", ""),
                            "-H", "Accept: " + headers.get("Accept", "*/*"),
                            "-H", "Accept-Language: en-US,en;q=0.9",
                            "-w", "%{http_code}", "-o", dest, url]
                status = subprocess.run(curl_cmd, check=False, capture_output=True,
                                        text=True).stdout.strip() or "000"
                body = None
                try:
                    status = int(status)
                except ValueError:
                    status = 0

            if status in (403, 429):
                log.warning("dl attempt %d/%d: HTTP %d (anti-bot) for %s — retrying",
                            attempt, retries, status, url)
            elif status >= 400:
                log.warning("dl attempt %d/%d: HTTP %d for %s",
                            attempt, retries, status, url)
            elif body is not None and len(body) < 200:
                log.warning("dl attempt %d/%d: response too small (%d bytes) for %s",
                            attempt, retries, len(body), url)
            else:
                if body is not None:
                    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                    # atomic write: never leave a truncated/partial CSV on disk
                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".")
                    try:
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(body)
                        os.replace(tmp, dest)
                    except Exception:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                        raise
                if not _looks_like_data(dest):
                    log.warning("dl attempt %d/%d: content of %s does not look like "
                                "data (size=%d) — retrying",
                                attempt, retries, dest, os.path.getsize(dest))
                else:
                    log.info("dl OK (%d bytes) %s", os.path.getsize(dest), url)
                    return True
        except Exception as ex:
            log.warning("dl attempt %d/%d: exception %r for %s",
                        attempt, retries, ex, url)

        if attempt < retries:
            time.sleep(5 * attempt)  # 5s, 15s backoff

    # Everything failed: refuse to leave stale data behind.
    if os.path.exists(dest):
        log.error("dl FAILED for %s — removing stale file %s (old data must not "
                  "be reported as fresh)", url, dest)
        try:
            os.remove(dest)
        except OSError as ex:
            log.error("could not remove %s: %r", dest, ex)
    else:
        log.error("dl FAILED for %s (no previous file existed)", url)
    return False


def _looks_like_data(path):
    """Heuristic: real CSV/JSON data, not an HTML error/anti-bot page."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024).decode("utf-8", errors="replace").lstrip()
    except OSError:
        return False
    if head.startswith("<!DOCTYPE") or head.startswith("<html"):
        return False  # error page or Cloudflare challenge
    return bool(head) and os.path.getsize(path) >= 200


def refresh_fixtures():
    """Download today's fixture lists from football-data.co.uk.

    Fallback: if the main fixtures.csv fails (403/429/timeout), we still have
    new_league_fixtures.csv. If both fail, the model gracefully degrades to
    historical data only (no upcoming matches) and logs the failure loudly.
    """
    os.makedirs(RAW, exist_ok=True)
    log.info("Refreshing fixtures from football-data.co.uk...")

    main_ok = dl("https://www.football-data.co.uk/fixtures.csv",
                  os.path.join(RAW, "fixtures_main.csv"))
    extra_ok = dl("https://www.football-data.co.uk/new_league_fixtures.csv",
                   os.path.join(RAW, "fixtures_extra.csv"))

    if main_ok and extra_ok:
        log.info("Fixtures refreshed: main + extra leagues both OK")
    elif main_ok or extra_ok:
        log.warning("Fixtures refreshed: only %s succeeded (other failed/stale)",
                    "main" if main_ok else "extra")
    else:
        log.error("Fixtures refresh FAILED: both main and extra downloads failed. "
                  "Today's predictions will use historical data only (no upcoming matches).")


def refresh_history(bootstrap=False):
    """Download historical match results for all leagues and seasons.

    Fallback: if football-data.co.uk is 403/429 for a specific division,
    we skip it cleanly (no stale file is left) and continue with the others.
    The model gracefully degrades to the leagues that did fetch.
    """
    os.makedirs(RAW, exist_ok=True)
    log.info("Refreshing historical data from football-data.co.uk...")

    # Extra leagues (single CSV per country)
    extra_ok = 0
    for c in EXTRA:
        if dl(f"https://www.football-data.co.uk/new/{c}.csv",
              os.path.join(RAW, f"extra_{c}.csv")):
            extra_ok += 1
    log.info("Extra leagues: %d/%d succeeded", extra_ok, len(EXTRA))

    # Main divisions (per season)
    seasons = current_seasons()
    if bootstrap:
        y = int(seasons[0][:2])
        seasons = sorted({f"{y-i:02d}{y-i+1:02d}" for i in range(3)} | set(seasons))

    main_ok = 0
    for s in seasons:
        for d in DIVS:
            p = os.path.join(RAW, f"main_{s}_{d}.csv")
            if dl(f"https://www.football-data.co.uk/mmz4281/{s}/{d}.csv", p):
                main_ok += 1
    total_main = len(seasons) * len(DIVS)
    log.info("Main divisions: %d/%d succeeded (seasons: %s)", main_ok, total_main, seasons)

    if extra_ok == 0 and main_ok == 0:
        log.error("History refresh FAILED: zero files downloaded (all blocked/404/timeout).")


def refresh_understat_if_stale(max_age_days=25):
    """Understat xG changes slowly; refresh at most ~monthly."""
    os.makedirs(PROC, exist_ok=True)
    sample = os.path.join(PROC, "understat_EPL.csv")
    fresh = os.path.exists(sample) and (time.time() - os.path.getmtime(sample)) < max_age_days * 86400
    if fresh:
        log.info("understat xG: fresh, skip")
        return
    step("understat xG refresh", "understat_fetch.py")


def step(name, script, args=None, timeout=None):
    """Run a script as a subprocess with timeout and logging."""
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, script)] + (args or []),
                           capture_output=True, text=True, timeout=timeout)
        elapsed = time.time() - t0
        if r.returncode == 0:
            log.info("[OK] %s (%.0fs) %s", name, elapsed, r.stdout.strip()[:250])
        else:
            log.error("[FAIL] %s (%.0fs) — returncode %d", name, elapsed, r.returncode)
            log.error("stdout: %s", r.stdout.strip()[:400])
            # هم ابتدا و هم انتهای stderr را لاگ می‌کنیم: اسکریپت‌هایی مثل
            # predict_today ده‌ها خط پیشرفت به stderr می‌ریزند و traceback واقعی
            # در انتهاست؛ اگر فقط ابتدا را لاگ کنیم علت خرابی هرگز دیده نمی‌شود.
            err = r.stderr.strip()
            if len(err) <= 1500:
                log.error("stderr: %s", err)
            else:
                log.error("stderr[head]: %s", err[:400])
                log.error("stderr[tail]: %s", err[-1100:])
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("[TIMEOUT] %s (>%ds) — skipped, continuing", name, timeout)
        return False
    except Exception as ex:
        log.error("[ERROR] %s: %r", name, ex)
        return False


def main():
    args = set(sys.argv[1:])
    os.makedirs(OUT, exist_ok=True)
    bootstrap = not glob.glob(os.path.join(RAW, "extra_*.csv"))

    log.info("=" * 70)
    log.info("Daily football prediction pipeline — starting")
    log.info("Arguments: %s", args if args else "(none)")
    log.info("=" * 70)

    # 1. data refresh
    refresh_fixtures()
    if "--fast" not in args:
        refresh_history(bootstrap=bootstrap)
        refresh_understat_if_stale()
        # settle yesterday's picks against the now-complete results and update
        # per-source score weights (used by merge_rank's consensus boost)
        step("settle past picks + score sources", "settle_picks.py", ["settle"], timeout=300)

    # 2. harvest external tips (fault-isolated per source)
    if "--no-harvest" not in args:
        step("harvest prediction sites (6)", "harvest_sites.py", timeout=420)
        step("harvest new sites (tips.gg, goalvertex)", "new_scrapers.py", timeout=420)
        step("harvest betmines (API)", "betmines_fetch.py", timeout=180)
        step("refresh injuries (transfermarkt)", "transfermarkt_injuries.py", timeout=300)
        # Agent writes tips_youtube.json / tips_telegram.json before calling this.
        if os.environ.get("TG_BOT_TOKEN"):
            step("telegram bot inbox (forwarded posts)", "tg_bot_inbox.py", timeout=90)
        if "--with-telegram-personal" in args:
            step("read personal telegram channels", "tg_harvest.py", ["--hours", "30"], timeout=420)

    # 3. model + merge + assistant layer + report
    step("model predictions (xG+FotMob+injuries)", "predict_today.py", timeout=420)
    step("merge + rank", "merge_rank.py", timeout=180)
    step("webapp dataset (forms/H2H/profiles)", "webapp_data.py", timeout=420)
    step("curated picks + reasons", "picks.py", timeout=120)
    step("snapshot picks to ledger", "settle_picks.py", ["snapshot"], timeout=60)
    if os.environ.get("AI_API_KEY"):
        step("AI analysis layer (pluggable)", "ai_summary.py", timeout=150)
    step("build Persian report", "report_gen.py", timeout=120)

    # 4. optional delivery
    if "--deliver" in args:
        step("deliver report", "deliver.py", ["all"], timeout=120)

    log.info("=" * 70)
    log.info("DONE. Deliverables:")
    log.info("  - %s", os.path.join(OUT, "report_fa.md"))
    log.info("  - %s", os.path.join(OUT, "ranked_options.json"))
    log.info("=" * 70)


if __name__ == "__main__":
    main()
