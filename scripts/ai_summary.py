# -*- coding: utf-8 -*-
"""Pluggable AI analysis layer — bring ANY AI API key, get Persian analysis.

If AI credentials are present in env, sends the day's curated picks to the
chosen LLM and asks for: (1) a short Persian daily brief, (2) 2-3 sentences of
polished logical analysis per pick (grounded ONLY in the provided data).
Fails soft: without a key or on any error, the pipeline continues unchanged.

Env (GitHub Secrets or skill credentials):
    AI_PROVIDER = openai | openrouter | gemini      (default: openai)
    AI_API_KEY  = sk-...
    AI_MODEL    = gpt-4o-mini | google/gemini-flash-1.5 | gemini-1.5-flash | ...

Providers use plain HTTPS chat APIs, so this runs anywhere (GitHub Actions,
any server, any notebook) — no vendor lock-in to this platform.

Output: output/ai_brief.json  {"daily_brief": str, "picks": {fixture_id: str}}
"""
import json
import os
import sys

import requests

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")

PROMPT = """تو یک تحلیلگر حرفه‌ای فوتبال هستی. بر اساس داده‌های زیر (و فقط همین داده‌ها — چیزی از خودت اختراع نکن):

{data}

خروجی را دقیقاً به‌صورت JSON بده با این ساختار:
{{"daily_brief": "جمع‌بندی کوتاه روز در ۲-۳ جمله فارسی، صادقانه و بدون اغراق",
  "picks": {{"<fixture_id>": "برای هر پیک ۲-۳ جمله تحلیل منطقی فارسی که دلایل داده‌شده را روان و حرفه‌ای روایت کند", ...}}}}

قوانین: لحن حرفه‌ای و دوستانه؛ هرگز واژه «تضمینی» یا «قطعی» استفاده نکن؛ اعداد را تغییر نده؛ اگر دلیل کافی نیست همان را بگو."""


def _extract_json(text):
    """Best-effort JSON extraction (some free models ignore json mode)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1] if a >= 0 <= b else text)


def _call_openai_style(base, key, model, prompt, extra_headers=None):
    payload = {"model": model,
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.4,
               "response_format": {"type": "json_object"}}
    hdrs = {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json", **(extra_headers or {})}
    r = requests.post(base, timeout=90, headers=hdrs, json=payload)
    if r.status_code == 400:  # model may not support response_format -> retry plain
        payload.pop("response_format")
        r = requests.post(base, timeout=90, headers=hdrs, json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_gemini(key, model, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    r = requests.post(url, timeout=90, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}})
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def run():
    key = os.environ.get("AI_API_KEY", "").strip()
    if not key:
        print("AI layer: no AI_API_KEY — skipped (pipeline works without it)")
        return False
    provider = os.environ.get("AI_PROVIDER", "openai").strip().lower()
    model = os.environ.get("AI_MODEL", "").strip() or \
        {"openai": "gpt-4o-mini", "openrouter": "google/gemini-flash-1.5",
         "gemini": "gemini-1.5-flash"}.get(provider, "gpt-4o-mini")

    cp = os.path.join(OUT, "curated_picks.json")
    if not os.path.exists(cp):
        print("AI layer: curated_picks.json missing — run picks.py first")
        return False
    with open(cp) as f:
        picks = json.load(f).get("picks", [])
    if not picks:
        print("AI layer: no picks today")
        return False

    compact = [{"fixture_id": p["fixture_id"], "match": f"{p['home']} vs {p['away']}",
                "league": p["league"], "pick": p["label_fa"], "prob": p["p"],
                "odds": p.get("odds"), "reasons": p["reasons"], "sources": p["sources"][:4]}
               for p in picks]
    prompt = PROMPT.format(data=json.dumps(compact, ensure_ascii=False, indent=1))

    try:
        custom_base = os.environ.get("AI_BASE_URL", "").strip()
        if custom_base:  # any OpenAI-compatible endpoint (Groq, Together, DeepSeek, Ollama...)
            text = _call_openai_style(custom_base.rstrip("/") + "/chat/completions",
                                      key, model, prompt)
        elif provider == "gemini":
            text = _call_gemini(key, model, prompt)
        elif provider == "openrouter":
            text = _call_openai_style("https://openrouter.ai/api/v1/chat/completions",
                                      key, model, prompt)
        else:
            text = _call_openai_style("https://api.openai.com/v1/chat/completions",
                                      key, model, prompt)
        data = _extract_json(text)
        assert isinstance(data.get("picks"), dict)
    except Exception as ex:
        print(f"AI layer failed (soft): {str(ex)[:200]}")
        return False

    with open(os.path.join(OUT, "ai_brief.json"), "w") as f:
        json.dump({"provider": provider, "model": model,
                   "daily_brief": str(data.get("daily_brief", ""))[:600],
                   "picks": {str(k): str(v)[:500] for k, v in data["picks"].items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"AI layer OK: {provider}/{model} — brief + {len(data['picks'])} pick analyses")
    return True


if __name__ == "__main__":
    ok = run()
    sys.exit(0)  # always soft
