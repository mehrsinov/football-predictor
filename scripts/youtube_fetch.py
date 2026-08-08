# -*- coding: utf-8 -*-
"""YouTube analysis layer — fills the reserved tips_youtube.json slot.

Pulls the latest videos from a CURATED list of channels (via each channel's
public RSS feed — no YouTube Data API key needed), asks Gemini to WATCH each
video and extract ONLY the match tips the video EXPLICITLY states, and writes
them in the canonical tip schema so merge_rank picks them up like any other
source. The per-video Persian summary rides along as `note` (rendered with 🎥).

Honesty (hard rule): Gemini is instructed to return a tip ONLY when the video
clearly backs a specific pick for a specific match; on any doubt it must omit.
Nothing here fabricates a fixture or a tip — a keyless/blocked/empty run just
writes an empty list and the pipeline continues untouched.

Config (all optional; empty → clean skip, never an error):
    Key   : YT_API_KEY | GEMINI_API_KEY | GOOGLE_API_KEY | AI_API_KEY
    Model : YT_MODEL                                  (default gemini-2.5-flash)
    Chans : YT_CHANNELS (CSV of UC.../@handle/URL) OR scripts/yt_channels.txt
    Caps  : YT_SINCE_HOURS(30) YT_MAX_PER_CHANNEL(3) YT_MAX_CHANNELS(12)
            YT_MAX_VIDEOS(25)

Output: output/tips_youtube.json  (always written, even as [], anti-staleness)
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = date.today().isoformat()

KEY = (os.environ.get("YT_API_KEY") or os.environ.get("GEMINI_API_KEY")
       or os.environ.get("GOOGLE_API_KEY") or os.environ.get("AI_API_KEY") or "").strip()
MODEL = (os.environ.get("YT_MODEL") or "gemini-2.5-flash").strip()

SINCE_HOURS = int(os.environ.get("YT_SINCE_HOURS", "30") or 30)
MAX_PER_CHANNEL = int(os.environ.get("YT_MAX_PER_CHANNEL", "3") or 3)
MAX_CHANNELS = int(os.environ.get("YT_MAX_CHANNELS", "12") or 12)
MAX_VIDEOS = int(os.environ.get("YT_MAX_VIDEOS", "25") or 25)

MARKETS_OK = {"1X2", "DC", "OU15", "OU25", "OU35", "BTTS", "CS"}

PROMPT = """تو یک تحلیلگر فوتبال هستی. این ویدیو یک پیش‌بینی/تحلیل فوتبالی است.
فقط و فقط تیپ‌هایی را که ویدیو **به‌صراحت** برای یک بازی مشخص مطرح کرده استخراج کن.
اگر ویدیو دربارهٔ یک بازی نظر روشن نداد، آن را نادیده بگیر — چیزی از خودت نساز.

خروجی را **دقیقاً** به‌صورت یک آرایهٔ JSON بده (بدون متن اضافه)، هر عضو:
{"home": "نام انگلیسی تیم میزبان", "away": "نام انگلیسی تیم مهمان",
 "league": "نام لیگ به انگلیسی در صورت ذکر", "market": "یکی از 1X2/DC/OU25/OU15/OU35/BTTS/CS",
 "pick": "انتخاب", "prob": عدد۰تا۱یانال, "note": "خلاصهٔ یک‌جمله‌ایِ دلیلِ ویدیو به فارسی"}

قواعد pick بر حسب market:
 - 1X2 → یکی از "1" (برد میزبان) / "X" (مساوی) / "2" (برد مهمان)
 - DC  → "1X" یا "X2" یا "12"
 - OU25/OU15/OU35 → "Over" یا "Under"
 - BTTS → "Yes" یا "No"
 - CS  → نتیجهٔ دقیق مثل "2-1"
نام تیم‌ها را حتماً انگلیسی و کامل بده (برای تطبیق لازم است). اگر هیچ تیپ صریحی نبود، آرایهٔ خالی [] بده."""


def _log(msg):
    sys.stderr.write(f"[youtube] {msg}\n")


def http_get(url, timeout=25):
    """Fetch text with curl_cffi impersonation; fall back to urllib. None on fail."""
    try:
        from curl_cffi import requests as cr
        for p in ("chrome124", "chrome110", "safari17_0"):
            try:
                r = cr.get(url, impersonate=p, timeout=timeout,
                           headers={"Accept-Language": "en-US,en;q=0.9"})
                if r.status_code == 200 and r.text:
                    return r.text
            except Exception:
                continue
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as ex:
        _log(f"GET failed {url[:60]}: {type(ex).__name__}")
        return None


def load_channels():
    """Curated channel ids/handles from env CSV or scripts/yt_channels.txt."""
    raw = os.environ.get("YT_CHANNELS", "")
    seeds = [s.strip() for s in raw.replace("\n", ",").split(",") if s.strip()]
    if not seeds:
        p = os.path.join(HERE, "yt_channels.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        seeds.append(line)
    # de-dup preserving order
    return list(dict.fromkeys(seeds))[:MAX_CHANNELS]


def resolve_channel_id(seed):
    """Return a UC... channelId. UC-id as-is; @handle/URL resolved via page."""
    s = seed.strip()
    if re.fullmatch(r"UC[0-9A-Za-z_-]{20,}", s):
        return s
    page = s if s.startswith("http") else f"https://www.youtube.com/{s.lstrip('/')}"
    html = http_get(page)
    if not html:
        return None
    for pat in (r'"channelId":"(UC[0-9A-Za-z_-]{20,})"',
                r'"externalId":"(UC[0-9A-Za-z_-]{20,})"',
                r'<meta itemprop="identifier" content="(UC[0-9A-Za-z_-]{20,})"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    _log(f"could not resolve channel: {s}")
    return None


def recent_videos(channel_id, cutoff):
    """Parse channel RSS → [{id,url,title,desc,published}] within cutoff."""
    xml = http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    if not xml:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    try:
        root = ET.fromstring(xml)
    except Exception as ex:
        _log(f"RSS parse fail {channel_id}: {ex}")
        return []
    vids = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns)
        pub = e.find("a:published", ns)
        title = e.find("a:title", ns)
        group = e.find("media:group", ns)
        desc_el = group.find("media:description", ns) if group is not None else None
        if vid is None or pub is None:
            continue
        try:
            when = datetime.fromisoformat(pub.text.replace("Z", "+00:00"))
        except Exception:
            continue
        if when < cutoff:
            continue
        vids.append({
            "id": vid.text,
            "url": f"https://www.youtube.com/watch?v={vid.text}",
            "title": (title.text if title is not None else "") or "",
            "desc": (desc_el.text if desc_el is not None else "") or "",
            "published": pub.text,
        })
        if len(vids) >= MAX_PER_CHANNEL:
            break
    return vids


def _extract_json_array(text):
    """Pull a JSON array out of an LLM reply (tolerant of fences/prose)."""
    if not text:
        return []
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b < a:
        return []
    try:
        data = json.loads(t[a:b + 1])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def analyze_video(video):
    """Ask Gemini to watch the video and return explicit tips (list of dicts).

    Tries the google-genai SDK first (native YouTube ingestion), then a REST
    fileData fallback, then a text-only pass over title+description. Any failure
    at every stage → [] (never fabricates)."""
    prompt = PROMPT
    # 1) google-genai SDK with native video ingestion
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=KEY)
        resp = client.models.generate_content(
            model=MODEL,
            contents=types.Content(parts=[
                types.Part(file_data=types.FileData(file_uri=video["url"])),
                types.Part(text=prompt),
            ]),
            config=types.GenerateContentConfig(
                temperature=0.2, response_mime_type="application/json"),
        )
        arr = _extract_json_array(resp.text or "")
        if arr:
            return arr
    except Exception as ex:
        _log(f"SDK ingest failed ({video['id']}): {type(ex).__name__}: {str(ex)[:120]}")

    # 2) REST fallback — fileData/fileUri part (v1beta)
    try:
        import requests
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{MODEL}:generateContent?key={KEY}")
        body = {"contents": [{"parts": [
            {"fileData": {"fileUri": video["url"]}},
            {"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
        r = requests.post(url, json=body, timeout=120)
        if r.status_code == 200:
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            arr = _extract_json_array(txt)
            if arr:
                return arr
        else:
            _log(f"REST ingest {video['id']} status {r.status_code}")
    except Exception as ex:
        _log(f"REST ingest failed ({video['id']}): {type(ex).__name__}")

    # 3) text-only fallback: title + description (honest, usually sparse)
    meta = f"عنوان ویدیو: {video['title']}\nتوضیحات: {video['desc'][:1500]}"
    try:
        import requests
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{MODEL}:generateContent?key={KEY}")
        body = {"contents": [{"parts": [{"text": prompt + "\n\n" + meta}]}],
                "generationConfig": {"temperature": 0.2,
                                     "responseMimeType": "application/json"}}
        r = requests.post(url, json=body, timeout=60)
        if r.status_code == 200:
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return _extract_json_array(txt)
    except Exception as ex:
        _log(f"text fallback failed ({video['id']}): {type(ex).__name__}")
    return []


def _norm_market_pick(market, pick):
    """Light pre-normalization; merge_rank.canon_tip does the strict pass."""
    m = str(market or "").upper().replace("O/U", "OU").replace(".", "").strip()
    p = str(pick or "").strip()
    if m not in MARKETS_OK:
        low = p.lower()
        mp = {"home": ("1X2", "1"), "draw": ("1X2", "X"), "away": ("1X2", "2"),
              "over": ("OU25", "Over"), "under": ("OU25", "Under"),
              "yes": ("BTTS", "Yes"), "no": ("BTTS", "No")}.get(low)
        if mp:
            m, p = mp
        elif re.fullmatch(r"\d+\s*-\s*\d+", p):
            m = "CS"
        else:
            return None, None
    return m, p


def to_tip(raw, source, url):
    home = str(raw.get("home") or "").strip()
    away = str(raw.get("away") or "").strip()
    if not home or not away or home.lower() == away.lower():
        return None
    market, pick = _norm_market_pick(raw.get("market"), raw.get("pick"))
    if not market or not pick:
        return None
    prob = raw.get("prob")
    try:
        prob = float(prob)
        prob = prob if 0.0 < prob <= 1.0 else None
    except (TypeError, ValueError):
        prob = None
    note = str(raw.get("note") or "").strip()[:120] or None
    league = str(raw.get("league") or "").strip() or None
    return {"source": source, "source_type": "youtube", "lang": "fa", "league": league,
            "home": home, "away": away, "match_date": TODAY,
            "market": market, "pick": pick, "prob": prob, "odds": None,
            "url": url, "note": note}


def run():
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "tips_youtube.json")

    def write(tips):
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(tips, f, indent=1, ensure_ascii=False)

    if not KEY:
        _log("no API key (YT_API_KEY/GEMINI_API_KEY/GOOGLE_API_KEY/AI_API_KEY) — skip")
        write([])
        return
    channels = load_channels()
    if not channels:
        _log("no channels configured (YT_CHANNELS / yt_channels.txt) — skip")
        write([])
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=SINCE_HOURS)
    tips, seen = [], set()
    budget = MAX_VIDEOS
    for seed in channels:
        if budget <= 0:
            break
        cid = resolve_channel_id(seed)
        if not cid:
            continue
        for v in recent_videos(cid, cutoff):
            if budget <= 0:
                break
            budget -= 1
            source = f"yt:{seed.lstrip('@')[:30]}"
            for raw in analyze_video(v):
                t = to_tip(raw, source, v["url"])
                if not t:
                    continue
                k = (t["home"].lower(), t["away"].lower(), t["market"], t["pick"], source)
                if k in seen:
                    continue
                seen.add(k)
                tips.append(t)
            _log(f"{source} {v['id']}: total tips so far {len(tips)}")

    write(tips)
    print(f"youtube tips: {len(tips)} from {len(channels)} channel(s), "
          f"{MAX_VIDEOS - budget} video(s) analyzed")


if __name__ == "__main__":
    try:
        run()
    except Exception as ex:
        _log(f"fatal (soft): {type(ex).__name__}: {ex}")
        try:
            with open(os.path.join(OUT, "tips_youtube.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
        except Exception:
            pass
    sys.exit(0)  # always soft — never breaks the pipeline
