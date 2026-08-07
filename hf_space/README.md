---
title: Football Predictor Bot
emoji: ⚽
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: "4.44.0"
app_file: app.py
pinned: false
---

# ربات تلگرام پیش‌بین فوتبال (Gradio Space)

ربات تحلیل‌گر فوتبال ۲۴ ساعته و رایگان.

## راه‌اندازی
۱. یک Space جدید با SDK **Gradio** بساز.
۲. دو فایل `app.py` و `requirements.txt` را از پوشه‌ی `hf_space/` بساز/آپلود کن.
۳. تب **Settings → Variables and secrets** این Secretها را اضافه کن:
   - `TG_BOT_TOKEN` (لازم)
   - `ADMIN_CHAT_ID` (اختیاری — فقط به خودت پاسخ دهد)
   - `AI_PROVIDER` + `AI_API_KEY` + `AI_MODEL` (اختیاری — خواندن عکس کوپن)
۴. Space خودش build و اجرا می‌شود؛ صفحه وضعیت که «running» شد، در تلگرام `/start` بده.

> ضدخواب: آدرس عمومی Space را در cron-job.org (رایگان) هر ۳۰ دقیقه ping کن.
