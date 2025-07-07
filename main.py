import os
import asyncio
from uuid import uuid4
from balethon import Client
from yt_dlp import YoutubeDL

# 🟢 توکن ربات Bale رو اینجا بذار
bot = Client("1011430416:V6rCwbls3JUS38Zq9GZrGfMeRF2VDuPtVMaVxEWH")

# کش موقت برای نگه داشتن نتایج جستجوی کاربران
search_cache = {}

def search_soundcloud(query):
    with YoutubeDL({"quiet": True}) as ydl:
        try:
            results = ydl.extract_info(f"scsearch5:{query}", download=False)["entries"]
            return results
        except Exception:
            return []

def delete_file(path):
    if os.path.exists(path):
        os.remove(path)

async def download_and_send(chat_id, url):
    filename = f"{uuid4()}.mp3"
    msg = await bot.send_message(chat_id, "⏳ در حال دانلود فایل...")

    def progress_hook(d):
        if d["status"] == "downloading":
            percent = d.get("_percent_str", "").strip()
            speed = d.get("_speed_str", "").strip()
            eta = d.get("_eta_str", "").strip()
            text = f"⬇️ در حال دانلود...\nدرصد: {percent}\nسرعت: {speed}\nزمان: {eta}"
            asyncio.create_task(bot.edit_message_text(chat_id, msg.id, text))

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": filename,
        "quiet": True,
        "progress_hooks": [progress_hook],
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        await bot.edit_message_text(chat_id, msg.id, "⛔ خطا در دانلود فایل.")
        return

    await bot.send_audio(chat_id, audio=filename)
    delete_file(filename)

@bot.on_message()
async def handle_message(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if not text:
        return await bot.send_message(chat_id, "لطفاً یک نام آهنگ بفرست!")

    results = search_soundcloud(text)
    if not results:
        return await bot.send_message(chat_id, "⚠️ نتیجه‌ای برای جستجو یافت نشد.")

    keyboard = []
    search_cache[chat_id] = {}

    for i, item in enumerate(results[:5]):
        title = item.get("title", "بدون عنوان")
        url = item.get("webpage_url")
        sid = str(uuid4())
        search_cache[chat_id][sid] = url
        keyboard.append([{
            "text": f"🎵 {title[:40]}",  # برای جلوگیری از طول زیاد
            "callback_data": f"dl|{sid}"
        }])

    await bot.send_message(
        chat_id,
        "🎶 نتایج یافت‌شده از SoundCloud:",
        reply_markup={"inline_keyboard": keyboard}
    )

@bot.on_callback_query()
async def handle_callback(callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data

    if not data.startswith("dl|"):
        return await callback_query.answer("❌ فرمان نامعتبر.")

    sid = data.split("|", 1)[1]

    if chat_id not in search_cache or sid not in search_cache[chat_id]:
        return await callback_query.answer("⛔ مورد یافت نشد.")

    url = search_cache[chat_id][sid]
    await callback_query.answer("⬇️ دانلود شروع شد...")
    await download_and_send(chat_id, url)

bot.run()
