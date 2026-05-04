import os
import sqlite3
import yt_dlp
from balethon import Client
from balethon.conditions import private
from balethon.objects import Message

# دریافت اطلاعات از متغیرهای محیطی که در گیت‌هاب سکرت تنظیم کرده‌اید
BOT_TOKEN = os.getenv("BOT_TOKEN")
archive_id_env = os.getenv("DB_CHANNEL_ID")
ARCHIVE_CHANNEL_ID = int(archive_id_env) if archive_id_env else 0

app = Client(BOT_TOKEN)

# راه‌اندازی و اتصال به دیتابیس SQLite
def init_db():
    conn = sqlite3.connect('archive.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tracks (
            yt_id TEXT PRIMARY KEY,
            title TEXT,
            message_id INTEGER
        )
    ''')
    conn.commit()
    return conn

db_conn = init_db()

def get_track_from_db(yt_id):
    cursor = db_conn.cursor()
    cursor.execute('SELECT message_id FROM tracks WHERE yt_id = ?', (yt_id,))
    result = cursor.fetchone()
    return result[0] if result else None

def save_track_to_db(yt_id, title, message_id):
    cursor = db_conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO tracks (yt_id, title, message_id) VALUES (?, ?, ?)', 
                   (yt_id, title, message_id))
    db_conn.commit()

@app.on_message(private)
async def handle_request(message: Message):
    query = message.text
    await message.reply("در حال جستجو و پردازش...")

    # تنظیمات yt-dlp برای استخراج بهترین کیفیت صوتی
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'extract_audio': True,
        'audio_format': 'mp3',
        'outtmpl': '%(id)s.%(ext)s',
        'default_search': 'ytsearch',
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # فقط اطلاعات را استخراج می‌کنیم تا ID را بررسی کنیم
            info = ydl.extract_info(query, download=False)
            
            # اگر جستجوی متنی بود، اولین نتیجه را بردار
            if 'entries' in info:
                info = info['entries'][0]
                
            yt_id = info['id']
            title = info['title']

            # بررسی وجود آهنگ در دیتابیس
            archived_msg_id = get_track_from_db(yt_id)

            if archived_msg_id:
                # اگر موجود بود، از کانال آرشیو فوروارد/کپی کن
                await message.reply("این آهنگ در آرشیو موجود است! در حال ارسال...")
                await app.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=ARCHIVE_CHANNEL_ID,
                    message_id=archived_msg_id
                )
            else:
                # اگر نبود، دانلود کن
                await message.reply("در حال دانلود از یوتیوب (ممکن است کمی طول بکشد)...")
                ydl.download([info['webpage_url']])
                
                filename = f"{yt_id}.mp3"
                
                # ۱. ارسال فایل به کانال آرشیو
                archive_msg = await app.send_document(
                    chat_id=ARCHIVE_CHANNEL_ID,
                    document=filename,
                    caption=f"Title: {title}\nID: {yt_id}"
                )
                
                # ۲. ذخیره آیدی پیام در دیتابیس
                save_track_to_db(yt_id, title, archive_msg.id)
                
                # ۳. ارسال فایل برای کاربر
                await app.send_document(
                    chat_id=message.chat.id,
                    document=filename,
                    caption=title
                )
                
                # ۴. پاک کردن فایل از روی سرور جهت جلوگیری از پر شدن فضا
                if os.path.exists(filename):
                    os.remove(filename)

    except Exception as e:
        await message.reply(f"خطایی رخ داد: {str(e)}")

if __name__ == "__main__":
    if not BOT_TOKEN or not ARCHIVE_CHANNEL_ID:
        print("Error: BOT_TOKEN or ARCHIVE_CHANNEL_ID is not set!")
    else:
        print("Bot is running...")
        app.run()
