# -*- coding: utf-8 -*-
"""Multilingual tip extraction from free-form Telegram/forum text.

Conservative by design: a tip is only extracted when a line contains two
team-like names separated by a versus marker AND a recognizable market keyword
in the same or adjacent line. Never invents; misses are acceptable.

Supported languages: en, ru, pt, es, tr, ar, fa (+ digit normalization).
"""
import re

FA_AR_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

VS_SEP = r"(?:\s+vs\.?\s+|\s+[-–—]\s+|\s+×\s+|\s+x\s+)"
TEAM = r"([A-Za-zÀ-ÿА-Яа-яЁё؀-ۿ][\w'’.À-ÿА-Яа-яЁё؀-ۿ ]{1,28}?)"
MATCH_RE = re.compile(TEAM + VS_SEP + TEAM, re.IGNORECASE)

ODDS_RE = re.compile(r"(?:@|кф\.?|coef|odd[s]?|oran|ضریب|كوته)\s*[:=]?\s*(\d(?:[.,]\d{1,2}))", re.IGNORECASE)

# market keyword table -> (market, pick)
MARKET_PATTERNS = [
    # over / under 2.5 (and 1.5 / 3.5)
    (r"(?:over|больше|тб|mais de|más de|üst|فوق|أكثر من|بالای|بیشتر از)\s*([123])[.,]5", lambda m: (f"OU{m.group(1)}5", "Over")),
    (r"(?:under|меньше|тм|menos de|alt(?:ı)?|زیر|أقل من|کمتر از)\s*([123])[.,]5", lambda m: (f"OU{m.group(1)}5", "Under")),
    # BTTS
    (r"(?:btts|gg\b|обе забьют|ambas marcam|ambos marcan|kg var|هر دو تیم گل|كلا الفريقين)", lambda m: ("BTTS", "Yes")),
    (r"(?:btts\s*no|ng\b|kg yok|обе не забьют|هر دو تیم گل نمی)", lambda m: ("BTTS", "No")),
    # double chance
    (r"(?:\b1x\b|двойной шанс п1|dupla hipótese casa|شانس دوبل میزبان)", lambda m: ("DC", "1X")),
    (r"(?:\bx2\b|двойной шанс п2|شانس دوبل مهمان)", lambda m: ("DC", "X2")),
    (r"\b12\b(?!\d)", lambda m: ("DC", "12")),
    # 1X2
    (r"(?:\bп1\b|\bms ?1\b|home win|casa vence|gana local|برد میزبان|فوز الأول|\b1\b(?=\s*(?:@|кф|odd|$)))", lambda m: ("1X2", "1")),
    (r"(?:\bп2\b|\bms ?2\b|away win|fora vence|gana visita|برد مهمان|فوز الثاني|\b2\b(?=\s*(?:@|кф|odd|$)))", lambda m: ("1X2", "2")),
    (r"(?:\bничья\b|\bdraw\b|\bempate\b|\bberabere\b|مساوی|تعادل|\bx\b(?=\s*(?:@|кф|odd|$)))", lambda m: ("1X2", "X")),
    # correct score
    (r"(?:correct score|точный счет|placar exato|skor|نتیجه دقیق|النتيجة)\s*[:=]?\s*(\d)\s*[-:]\s*(\d)", lambda m: ("CS", f"{m.group(1)}-{m.group(2)}")),
    # asian handicap (coarse)
    (r"(?:ah|handicap|фора|هندیکپ)\s*([+-]?\d(?:[.,]5)?)", lambda m: ("AH", m.group(1).replace(",", "."))),
]
MARKET_RES = [(re.compile(p, re.IGNORECASE), fn) for p, fn in MARKET_PATTERNS]

NOISE = re.compile(r"(fixed|100%|گارانتی|تضمینی|vip کانال|جوین شو|join now|promo|бонус|casino|کازینو)", re.IGNORECASE)


def clean(text):
    return (text or "").translate(FA_AR_DIGITS).replace("‌", " ").replace("*", " ").replace("#", " ")


def extract_tips(text, source, source_type="telegram", lang="mixed", url=None, match_date=None):
    """Return list of canonical tip dicts found in a message text."""
    text = clean(text)
    if not text or NOISE.search(text):
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    tips = []
    for i, ln in enumerate(lines):
        m = MATCH_RE.search(ln)
        if not m:
            continue
        home, away = m.group(1).strip(" .-"), m.group(2).strip(" .-")
        if len(home) < 3 or len(away) < 3 or home.lower() == away.lower():
            continue
        window = ln[m.end():] + " || " + (lines[i + 1] if i + 1 < len(lines) else "")
        found = None
        for rx, fn in MARKET_RES:
            mk = rx.search(window)
            if mk:
                found = fn(mk)
                break
        if not found:
            continue
        market, pick = found
        odds = None
        om = ODDS_RE.search(window)
        if om:
            try:
                odds = float(om.group(1).replace(",", "."))
                if not (1.01 <= odds <= 30):
                    odds = None
            except ValueError:
                pass
        tips.append({"source": source, "source_type": source_type, "lang": lang,
                     "league": None, "home": home, "away": away,
                     "match_date": match_date, "market": market, "pick": pick,
                     "prob": None, "odds": odds,
                     "note": ln[:80], "url": url})
    # dedupe within message
    seen, out = set(), []
    for t in tips:
        k = (t["home"].lower(), t["away"].lower(), t["market"], t["pick"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out
