# ربات تلگرام پیش‌بین فوتبال — روی Hugging Face Space رایگان (همیشه‌روشن)
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && rm -rf /var/lib/apt/lists/*

# دانلود آخرین نسخه کد (همان بسته عمومی که گیت‌هاب هم استفاده می‌کند)
RUN curl -sL "https://pub.hyperagent.com/api/published/pbf01KYAAF89P_NVQAQCXZDVHM48HW/fpbot-v7-final.zip" -o code.zip \
    && unzip -o code.zip -d _code && cp -r _code/scripts . && cp _code/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# پورت سلامت برای بیدارماندن Space (HF نیاز به یک پورت باز دارد)
EXPOSE 7860
# اجرای هم‌زمان: یک وب‌سرور سبک برای سلامت + ربات long-polling
CMD ["bash","-lc","cd scripts && (python3 -m http.server 7860 &) && python3 bot.py"]
