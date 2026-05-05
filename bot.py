import asyncio
import os
import re
import sqlite3
from pathlib import Path
import yt_dlp
from balethon import Client
from balethon.conditions import private, command, text
from balethon.objects import InlineKeyboard

# ===================== تنظیمات =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BALE_BOT_TOKEN")
CACHE_CHANNEL_ID = int(os.getenv("CACHE_CHANNEL_ID", "-1000000000000"))

TEMP_DIR = Path("temp_soundcloud")
TEMP_DIR.mkdir(exist_ok=True)
DB_PATH = "cache.db"

bot = Client(BOT_TOKEN)
BOT_USERNAME = "abraava_bot" 


# ===================== دیتابیس =====================
class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        fields = [
            "uuid TEXT PRIMARY KEY", "title TEXT", "uploader TEXT", "genre TEXT",
            "upload_date TEXT", "webpage_url TEXT", "thumbnail TEXT", "cache_msg_id TEXT",
            "duration TEXT"
        ]
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(f"CREATE TABLE IF NOT EXISTS tracks ({', '.join(fields)})")
            c.execute("PRAGMA table_info(tracks)")
            cols = [r[1] for r in c.fetchall()]
            needed_cols = [f.split()[0] for f in fields]
            if set(cols) != set(needed_cols):
                c.execute("DROP TABLE IF EXISTS tracks")
                c.execute(f"CREATE TABLE tracks ({', '.join(fields)})")
            conn.commit()

    def run_query(self, query, params=(), fetch=False, fetchone=False):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            if fetchone:
                return dict(c.fetchone() or {})
            if fetch:
                return [dict(r) for r in c.fetchall()]
            conn.commit()

db = DatabaseManager(DB_PATH)


def build_caption(track, bot_user):
    return (
        f"🎧 *{track.get('title','نامشخص')}*\n"
        f"🎤 هنرمند: *{track.get('uploader','نامشخص')}*\n"
        f"📅 سال: {track.get('upload_date','نامشخص')}\n"
        f"🎸 ژانر: {track.get('genre','نامشخص')}\n"
        f"⏱ مدت: {track.get('duration','نامشخص')}\n"
        f"🔗 [لینک اصلی]({track.get('webpage_url','نامشخص')})\n\n"
        f"🤖 @{bot_user}"
    )


# =================== توابع ساندکلاود ==================
def get_soundcloud_info(url):
    ydl_opts = {"quiet": True, "extract_flat": False}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info

def download_soundcloud_track(url):
    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "outtmpl": str(TEMP_DIR / "%(id)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = str(TEMP_DIR / f"{info['id']}.mp3") 
        return filepath, info

async def search_soundcloud(query, max_results=10):
    results = []
    ydl_opts = {"quiet": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"scsearch{max_results}:{query}", download=False)
        for e in info.get("entries", []):
            results.append({
                "id": e.get("id"),
                "title": e.get("title", "بدون نام"),
                "uploader": e.get("uploader", "نامشخص"),
                "webpage_url": e.get("url", ""),
                "thumbnail": e.get("thumbnail", "")
            })
    return results


# =================== هندلر start ==================
@bot.on_message(private & command("start"))
async def start_handler(client, message):
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await client.get_me()
        BOT_USERNAME = me.username
    
    txt = (
        "🎶 به ربات دانلودر ساندکلاود خوش آمدید!\n\n"
        "🔗 ارسال لینک: آهنگ را با کاور و جزئیات دریافت کنید.\n"
        "🕵️ ارسال متن: جستجوی آلبوم‌ها و ترک‌ها.\n\n"
        f"💡 در گروه‌ها می‌توانید با منشن کردن ربات (مثلاً `@ {BOT_USERNAME} جستجو`) از آن استفاده کنید."
    )
    await message.reply(txt)


# =================== هندلر لینک، جستجو و گروه‌ها ==================
@bot.on_message(text) # شرط private حذف شد تا پیام‌های گروه را هم بگیرد
async def handle_text(client, message):
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await client.get_me()
        BOT_USERNAME = me.username

    content = message.text.strip()
    
    # بررسی پیام در گروه یا کانال (آیا ربات منشن شده است؟)
    if message.chat.type != "private":
        mention = f"@{BOT_USERNAME}"
        if mention not in content:
            return # اگر ربات منشن نشده بود، پیام را نادیده بگیر
        # حذف آیدی ربات از متن پیام تا فقط دستور/لینک باقی بماند
        content = content.replace(mention, "").strip()
        if not content:
            return

    # صرف‌نظر از دستورات
    if content.startswith("/"):
        return

    # اگر لینک ساندکلاود است
    if "soundcloud.com" in content:
        url_match = re.search(r"(https?://[^\s]+)", content)
        if not url_match:
            return await message.reply("❌ لینک نامعتبر!")
        url = url_match.group(1)

        row = db.run_query("SELECT * FROM tracks WHERE webpage_url=?", (url,), fetchone=True)
        if row and row.get("cache_msg_id"):
            return await client.send_document(message.chat.id, row["cache_msg_id"], caption=build_caption(row, BOT_USERNAME))

        msg = await message.reply("⏳ در حال دانلود و آماده‌سازی...")
        loop = asyncio.get_event_loop()
        try:
            filepath, info = await loop.run_in_executor(None, download_soundcloud_track, url)
        except Exception as e:
            return await msg.edit_text(f"❌ خطا: {e}")

        meta = {
            "uuid": f"sc_{info['id']}",
            "title": info.get("title", ""),
            "uploader": info.get("uploader", ""),
            "genre": info.get("genre", ""),
            "upload_date": str(info.get("upload_date", ""))[:4],
            "webpage_url": info.get("webpage_url", url),
            "thumbnail": info.get("thumbnail", ""),
            "duration": str(info.get("duration", "")),
        }
        caption = build_caption(meta, BOT_USERNAME)

        with open(filepath, "rb") as f:
            sent_msg = await client.send_audio(CACHE_CHANNEL_ID, f, caption=caption)
        
        meta["cache_msg_id"] = str(sent_msg.audio.id)
        db.run_query(f"INSERT OR REPLACE INTO tracks ({','.join(meta.keys())}) VALUES ({','.join(['?'] * len(meta))})", tuple(meta.values()))

        await client.send_audio(message.chat.id, sent_msg.audio.id, caption=caption)
        await msg.delete()

        if os.path.exists(filepath):
            os.remove(filepath)
        return

    # اگر جستجوی متنی است
    msg = await message.reply("🔍 در حال جستجو...")
    results = await search_soundcloud(content, 15)
    if not results:
        return await msg.edit_text("😔 موردی یافت نشد.")

    buttons = [[(f"🎧 {item['title'][:20]}", f"show:{item['webpage_url']}")] for item in results[:10]]
    if len(results) > 10:
        buttons.append([("➡️ بعدی", f"page:{content}:1")])
    await msg.edit_text(f"🎯 نتایج برای *{content}*", InlineKeyboard(*buttons))


# =================== هندلر دکمه‌های شیشه‌ای ==================
@bot.on_callback_query()
async def handle_callback(client, callback_query):
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await client.get_me()
        BOT_USERNAME = me.username

    data = callback_query.data

    if data.startswith("page:"):
        _, keyword, page_index = data.split(":")
        page_index = int(page_index)
        results = await search_soundcloud(keyword, 30)
        start = page_index * 10
        end = start + 10
        buttons = [[(f"🎧 {item['title'][:20]}", f"show:{item['webpage_url']}")] for item in results[start:end]]
        nav_buttons = []
        if start > 0: nav_buttons.append(("⬅️ قبلی", f"page:{keyword}:{page_index - 1}"))
        if end < len(results): nav_buttons.append(("➡️ بعدی", f"page:{keyword}:{page_index + 1}"))
        if nav_buttons: buttons.append(nav_buttons)
        await callback_query.message.edit_text(f"🎯 نتایج برای *{keyword}* (صفحه {page_index + 1})", InlineKeyboard(*buttons))

    elif data.startswith("show:"):
        url = data.split(":", 1)[1]
        
        row = db.run_query("SELECT * FROM tracks WHERE webpage_url=?", (url,), fetchone=True)
        if row:
            return await callback_query.message.reply(build_caption(row, BOT_USERNAME), InlineKeyboard([("⬇️ دریافت", f"dl:{url}")]))

        msg = await callback_query.message.reply("⏳ دریافت اطلاعات...")
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, get_soundcloud_info, url)
        except Exception as e:
            return await msg.edit_text(f"خطا: {e}")

        meta = {
            "uuid": f"sc_{info['id']}",
            "title": info.get("title", ""),
            "uploader": info.get("uploader", ""),
            "genre": info.get("genre", ""),
            "upload_date": str(info.get("upload_date", ""))[:4],
            "webpage_url": info.get("webpage_url", url),
            "thumbnail": info.get("thumbnail", ""),
            "duration": str(info.get("duration", "")),
        }
        db.run_query(f"INSERT OR REPLACE INTO tracks ({','.join(meta.keys())}) VALUES ({','.join(['?'] * len(meta))})", tuple(meta.values()))
        await msg.edit_text(build_caption(meta, BOT_USERNAME), InlineKeyboard([("⬇️ دریافت", f"dl:{url}")]))

    elif data.startswith("dl:"):
        url = data.split(":", 1)[1]
        msg = await callback_query.message.reply("⬇️ در حال پردازش فایل...")

        row = db.run_query("SELECT * FROM tracks WHERE webpage_url=?", (url,), fetchone=True)
        if row and row.get("cache_msg_id"):
            await client.send_document(callback_query.message.chat.id, row["cache_msg_id"], caption=build_caption(row, BOT_USERNAME))
            await msg.delete()
            return

        loop = asyncio.get_event_loop()
        try:
            filepath, info = await loop.run_in_executor(None, download_soundcloud_track, url)
        except Exception as e:
            await msg.edit_text(f"❌ خطا: {e}")
            return

        meta = {
            "uuid": f"sc_{info['id']}",
            "title": info.get("title", ""),
            "uploader": info.get("uploader", ""),
            "genre": info.get("genre", ""),
            "upload_date": str(info.get("upload_date", ""))[:4],
            "webpage_url": info.get("webpage_url", url),
            "thumbnail": info.get("thumbnail", ""),
            "duration": str(info.get("duration", "")),
        }
        caption = build_caption(meta, BOT_USERNAME)

        with open(filepath, "rb") as f:
            sent_msg = await client.send_audio(CACHE_CHANNEL_ID, f, caption=caption)
            
        meta["cache_msg_id"] = str(sent_msg.audio.id)
        db.run_query(f"INSERT OR REPLACE INTO tracks ({','.join(meta.keys())}) VALUES ({','.join(['?'] * len(meta))})", tuple(meta.values()))
        
        await client.send_audio(callback_query.message.chat.id, sent_msg.audio.id, caption=caption)
        await msg.delete()
        
        if os.path.exists(filepath):
            os.remove(filepath)

# =================== اجرا =====================
if __name__ == "__main__":
    bot.run()
