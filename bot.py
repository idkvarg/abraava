"""
abraava_bot_balethon.py
Abraava Music Bot - recoded with balethon.ir
All features: iTunes API, SQLite cache, YT Music downloader, ID3 tagging, DB channel storage.
"""

import logging
import json
import time
import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiohttp
import aiosqlite
import yt_dlp
from ytmusicapi import YTMusic
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error

# balethon imports
from balethon import Bot, objects
from balethon.objects import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from balethon.support import InputFile

# 8‑method downloader module (assumed available)
from youtube_downloader import download_audio

# ---------- Configuration ----------
ITUNES_BASE_URL = "https://itunes.apple.com"
DB_PATH = Path("cache.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "1011430416:5JY8CU9nGwYtVz0ahfDEIkJyCkVTUCAhLXQ")
DB_CHANNEL_ID = os.environ.get("DB_CHANNEL_ID", None)   # channel ID for permanent storage
ITEMS_PER_PAGE = 10
YT = None

BOT_NAME = "ابرآوا"
FOOTER = "\n\n@abraava_bot\n@abraava"

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("bot.log", encoding="utf-8"),
                              logging.StreamHandler()])
logger = logging.getLogger("AbraavaBot")

# ---------- Async SQLite Cache ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                last_updated INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audio_cache (
                track_id INTEGER PRIMARY KEY,
                channel_message_id INTEGER NOT NULL
            )
        """)
        await db.commit()
    logger.info("Database initialised.")

async def get_cached(id: str) -> Optional[Dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM cache WHERE id = ?", (id,)) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None

async def set_cached(id: str, type: str, data: Dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO cache (id, type, data, last_updated) VALUES (?, ?, ?, ?)",
                         (id, type, json.dumps(data), int(time.time())))
        await db.commit()

async def delete_cached(id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cache WHERE id = ?", (id,))
        await db.commit()

async def is_cached(id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM cache WHERE id = ?", (id,)) as cursor:
            return await cursor.fetchone() is not None

async def get_audio_cache(track_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_message_id FROM audio_cache WHERE track_id = ?", (track_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_audio_cache(track_id: int, channel_message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO audio_cache (track_id, channel_message_id) VALUES (?, ?)",
                         (track_id, channel_message_id))
        await db.commit()

# ---------- Shared HTTP Session ----------
class HttpClient:
    session: Optional[aiohttp.ClientSession] = None

    @classmethod
    async def get_session(cls):
        if cls.session is None or cls.session.closed:
            cls.session = aiohttp.ClientSession()
        return cls.session

    @classmethod
    async def close(cls):
        if cls.session and not cls.session.closed:
            await cls.session.close()

# ---------- iTunes API ----------
async def fetch_itunes(endpoint: str, params: dict) -> Optional[Dict]:
    session = await HttpClient.get_session()
    url = f"{ITUNES_BASE_URL}/{endpoint}"
    try:
        async with session.get(url, params=params, ssl=False) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"iTunes API status {resp.status} for {url}")
    except Exception as e:
        logger.error(f"iTunes fetch error: {e}")
    return None

async def search_itunes(term: str, entity: Optional[str] = None, limit: int = 50) -> Optional[Dict]:
    params = {"term": term, "media": "music", "limit": limit, "country": "US"}
    if entity:
        params["entity"] = entity
    return await fetch_itunes("search", params)

async def lookup_itunes(id: int, entity: Optional[str] = None) -> Optional[Dict]:
    params = {"id": id, "country": "US"}
    if entity:
        params["entity"] = entity
    return await fetch_itunes("lookup", params)

# ---------- Crawlers & caching ----------
async def crawl_artist_albums(artist_id: int, status_msg: Optional[Message] = None):
    cache_id = f"artist_albums:{artist_id}"
    if await is_cached(cache_id):
        return
    if status_msg:
        await status_msg.edit_text(f"⏳ *در حال دریافت آلبوم‌های هنرمند...*{FOOTER}")
    data = await lookup_itunes(artist_id, "album")
    if data and data.get("resultCount", 0):
        albums = [item["collectionId"] for item in data["results"]
                  if item.get("wrapperType") == "collection" and item.get("collectionType") == "Album"]
        for aid in albums:
            if not await is_cached(f"album:{aid}"):
                album_data = await lookup_itunes(aid)
                if album_data:
                    await set_cached(f"album:{aid}", "album", album_data)
        await set_cached(cache_id, "artist_albums", {"albums": albums})

async def get_artist(artist_id: int, status_msg: Optional[Message] = None) -> Optional[Dict]:
    cache_id = f"artist:{artist_id}"
    cached = await get_cached(cache_id)
    if cached:
        return cached
    if status_msg:
        await status_msg.edit_text(f"⏳ *در حال دریافت اطلاعات هنرمند...*{FOOTER}")
    data = await lookup_itunes(artist_id)
    if data and data.get("results"):
        await set_cached(cache_id, "artist", data)
        await crawl_artist_albums(artist_id, status_msg)
        return data
    return None

async def crawl_album_tracks(album_id: int, status_msg: Optional[Message] = None):
    cache_id = f"album_tracks:{album_id}"
    if await is_cached(cache_id):
        return
    if status_msg:
        await status_msg.edit_text(f"⏳ *در حال دریافت آهنگ‌های آلبوم...*{FOOTER}")
    data = await lookup_itunes(album_id, "song")
    if data and data.get("resultCount"):
        tracks = [item["trackId"] for item in data["results"]
                  if item.get("wrapperType") == "track" and item.get("kind") == "song"]
        for tid in tracks:
            if not await is_cached(f"track:{tid}"):
                track_data = await lookup_itunes(tid)
                if track_data:
                    await set_cached(f"track:{tid}", "track", track_data)
        await set_cached(cache_id, "album_tracks", {"tracks": tracks})

async def get_album(album_id: int, status_msg: Optional[Message] = None) -> Optional[Dict]:
    cache_id = f"album:{album_id}"
    cached = await get_cached(cache_id)
    if cached:
        return cached
    if status_msg:
        await status_msg.edit_text(f"⏳ *در حال دریافت اطلاعات آلبوم...*{FOOTER}")
    data = await lookup_itunes(album_id)
    if data and data.get("results"):
        await set_cached(cache_id, "album", data)
        await crawl_album_tracks(album_id, status_msg)
        return data
    return None

async def get_track(track_id: int, status_msg: Optional[Message] = None) -> Optional[Dict]:
    cache_id = f"track:{track_id}"
    cached = await get_cached(cache_id)
    if cached:
        return cached
    if status_msg:
        await status_msg.edit_text(f"⏳ *در حال دریافت اطلاعات آهنگ...*{FOOTER}")
    data = await lookup_itunes(track_id)
    if data and data.get("results"):
        await set_cached(cache_id, "track", data)
        return data
    return None

# ---------- YouTube Music ----------
async def search_youtube_track(query: str) -> Optional[str]:
    global YT
    if YT is None:
        YT = YTMusic()
    try:
        results = YT.search(query, filter="songs", limit=1)
        if results and isinstance(results, list) and results:
            return results[0].get("videoId")
    except Exception as e:
        logger.error(f"YTMusic search error: {e}")
    return None

# ---------- ID3 Tagger ----------
def tag_mp3(file_path: Path, title: str, artist: str, album: str, cover_bytes: bytes):
    try:
        try:
            audio = ID3(file_path)
        except error:
            audio = ID3()
        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist))
        if album:
            audio.add(TALB(encoding=3, text=album))
        if cover_bytes:
            audio.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_bytes))
        audio.save(file_path, v2_version=3)
        logger.info(f"Metadata added to {title}")
    except Exception as e:
        logger.error(f"Tagging failed: {e}")

# ---------- Download & Cache ----------
async def send_cached_or_download(bot: Bot, chat_id: int, track_id: int, status_msg: Optional[Message] = None):
    if not status_msg:
        status_msg = await bot.send_message(chat_id, f"⏳ *در حال آماده‌سازی دانلود...*{FOOTER}")

    # Check DB channel cache
    channel_msg_id = await get_audio_cache(track_id)
    if channel_msg_id and DB_CHANNEL_ID:
        try:
            await bot.forward_message(chat_id, DB_CHANNEL_ID, channel_msg_id)
            await status_msg.edit_text(f"✅ آهنگ از دیتابیس {BOT_NAME} دریافت شد.{FOOTER}")
            return
        except Exception as e:
            logger.error(f"Forward failed: {e}")

    # Fetch track info
    track_data = await get_track(track_id, status_msg)
    if not track_data or not track_data.get("results"):
        await status_msg.edit_text(f"❌ خطا در دریافت اطلاعات آهنگ.{FOOTER}")
        return

    track = track_data["results"][0]
    t_name = track.get("trackName", "Unknown Title")
    a_name = track.get("artistName", "Unknown Artist")
    album_name = track.get("collectionName", "")
    cover_url = get_high_res_artwork(track.get("artworkUrl100"), 600)

    query = f"{t_name} {a_name}"
    await status_msg.edit_text(f"🔍 جستجوی سورس باکیفیت در یوتیوب موزیک...{FOOTER}")
    video_id = await search_youtube_track(query)
    if not video_id:
        await status_msg.edit_text(f"❌ لینک یوتیوب موزیک یافت نشد.{FOOTER}")
        return

    video_url = f"https://music.youtube.com/watch?v={video_id}"
    await status_msg.edit_text(f"⏳ دانلود و آماده‌سازی (۸ روش ضدتحریم)...{FOOTER}")

    try:
        mp3_path = await asyncio.get_event_loop().run_in_executor(None, download_audio, video_url)
        if mp3_path is None:
            await status_msg.edit_text(f"❌ دانلود با شکست مواجه شد — همه ۸ روش ناموفق.{FOOTER}")
            return

        # Download cover
        cover_bytes = None
        if cover_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(cover_url) as resp:
                    if resp.status == 200:
                        cover_bytes = await resp.read()

        # Tag MP3
        await asyncio.get_event_loop().run_in_executor(None, tag_mp3, mp3_path, t_name, a_name, album_name, cover_bytes)

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        caption = f"🎵 {t_name}\n🎤 {a_name}\n📀 {album_name}\n🔊 MP3 320 kbps | {file_size_mb:.1f} MB{FOOTER}"

        # Upload to DB channel if configured
        if DB_CHANNEL_ID:
            try:
                await status_msg.edit_text(f"☁️ آپلود در سرور ابری {BOT_NAME}...{FOOTER}")
                with open(mp3_path, "rb") as f:
                    audio_bytes = f.read()
                audio_input = InputFile(audio_bytes, file_name=f"{t_name} - {a_name}.mp3")
                db_msg = await bot.send_audio(DB_CHANNEL_ID, audio_input, caption=caption)
                if db_msg and db_msg.message_id:
                    await set_audio_cache(track_id, db_msg.message_id)
                    await bot.forward_message(chat_id, DB_CHANNEL_ID, db_msg.message_id)
                    await status_msg.edit_text(f"✅ دانلود و ذخیره شد.{FOOTER}")
            except Exception as e:
                logger.error(f"DB channel upload error: {e}")
                # Fallback: send directly
                with open(mp3_path, "rb") as f:
                    audio_bytes = f.read()
                audio_input = InputFile(audio_bytes, file_name=f"{t_name} - {a_name}.mp3")
                await bot.send_audio(chat_id, audio_input, caption=caption)
                await status_msg.edit_text(f"✅ آهنگ مستقیماً ارسال شد (خطا در دیتابیس).{FOOTER}")
        else:
            with open(mp3_path, "rb") as f:
                audio_bytes = f.read()
            audio_input = InputFile(audio_bytes, file_name=f"{t_name} - {a_name}.mp3")
            await bot.send_audio(chat_id, audio_input, caption=caption)
            await status_msg.edit_text(f"✅ دانلود و ارسال با موفقیت انجام شد.{FOOTER}")

        mp3_path.unlink(missing_ok=True)

    except Exception as e:
        logger.exception("Download error")
        await status_msg.edit_text(f"❌ خطا در عملیات: {e}{FOOTER}")

async def send_voice_preview(bot: Bot, chat_id: int, track_id: int):
    status_msg = await bot.send_message(chat_id, f"⏳ دریافت پیش‌نمایش...{FOOTER}")
    track_data = await get_track(track_id)
    if not track_data or not track_data.get("results"):
        await status_msg.edit_text(f"❌ اطلاعات آهنگ یافت نشد.{FOOTER}")
        return
    track = track_data["results"][0]
    preview_url = track.get("previewUrl")
    if not preview_url:
        await status_msg.edit_text(f"❌ پیش‌نمایشی برای این آهنگ موجود نیست.{FOOTER}")
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(preview_url) as resp:
                if resp.status == 200:
                    audio_bytes = await resp.read()
                    voice_input = InputFile(audio_bytes, file_name="preview.m4a")
                    await bot.send_voice(chat_id, voice_input, caption=f"🎧 پیش‌نمایش {track.get('trackName')}{FOOTER}")
                    await status_msg.delete()
                else:
                    await status_msg.edit_text(f"❌ خطا در دریافت پیش‌نمایش.{FOOTER}")
    except Exception as e:
        logger.error(f"Preview error: {e}")
        await status_msg.edit_text(f"❌ خطا در ارسال پیش‌نمایش.{FOOTER}")

# ---------- Helpers ----------
def format_duration(milliseconds: int) -> str:
    if not milliseconds:
        return "نامشخص"
    minutes = milliseconds // 60000
    seconds = (milliseconds % 60000) // 1000
    return f"{minutes}:{seconds:02d}"

def get_high_res_artwork(url: str, size: int = 600) -> str:
    return url.replace("100x100bb", f"{size}x{size}bb") if url else ""

def create_pagination_row(prefix: str, current: int, total: int) -> List[InlineKeyboardButton]:
    row = []
    if current > 1:
        row.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"{prefix}:{current - 1}"))
    row.append(InlineKeyboardButton(text=f"صفحه {current} از {total}", callback_data="ignore"))
    if current < total:
        row.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"{prefix}:{current + 1}"))
    return row

def generate_search_hash(type_: str, term: str) -> str:
    return hashlib.md5(f"{type_}:{term}".encode()).hexdigest()[:10]

# ---------- Bot Handlers ----------
bot = Bot(token=BOT_TOKEN)

@bot.on_ready()
async def on_ready():
    logger.info(f"Bot {bot.user.username} (Abraava) started with balethon!")
    await init_db()

@bot.on_message()
async def on_message(message: Message):
    if not message.text:
        return
    is_group = message.chat.type in ["group", "supergroup", "channel"]
    text = message.text

    if is_group:
        bot_username = (await bot.get_me()).username
        mention = f"@{bot_username}"
        if mention not in text:
            return
        text = text.replace(mention, "").strip()

    if text.startswith("/start"):
        await message.reply(
            f"🎵 *به ربات {BOT_NAME} خوش آمدید!*\n\n"
            "دستورات:\n"
            "`/search artist:<نام>` - جستجوی هنرمند\n"
            "`/search album:<نام>` - جستجوی آلبوم\n"
            "`/search track:<نام>` - جستجوی آهنگ\n"
            "`/search <نام>` - جستجوی ترکیبی\n\n"
            "ویژگی‌ها:\n"
            "• کش و دیتابیس اختصاصی\n"
            "• متادیتا کامل (کاور، نام، خواننده)\n"
            "• پیش‌نمایش صوتی\n"
            "• دانلود از یوتیوب موزیک با ۸ روش ضدتحریم\n"
            f"{FOOTER}"
        )
    elif text.startswith("/help"):
        await message.reply(f"🛠 *راهنما*\nاز `/search` استفاده کنید.\nمثال: `/search ed sheeran`\n{FOOTER}")
    elif text.startswith("/about"):
        await message.reply(f"ℹ️ *درباره {BOT_NAME}*\nربات جستجو و دانلود موسیقی با کیفیت بالا.\n{FOOTER}")
    elif text.startswith("/search"):
        parts = text.split(" ", 1)
        if len(parts) < 2:
            await message.reply(f"❌ عبارت جستجو را وارد کنید.\nمثال: `/search artist:Taylor Swift`{FOOTER}")
            return
        query = parts[1].strip()
        if ":" in query:
            type_, term = query.split(":", 1)
            type_ = type_.lower()
            if type_ not in ("artist", "album", "track"):
                await message.reply(f"❌ نوع نامعتبر. از artist, album, track استفاده کنید.{FOOTER}")
                return
        else:
            type_, term = "all", query

        entity_map = {"artist": "musicArtist", "album": "album", "track": "musicTrack"}
        type_fa = {"artist": "هنرمند", "album": "آلبوم", "track": "آهنگ", "all": "همه"}
        status_msg = await message.reply(f"🔍 *جستجوی {type_fa[type_]}: {term}...*{FOOTER}")

        search_id = generate_search_hash(type_, term)
        cache_key = f"search:{search_id}"

        if type_ == "all":
            results = await search_itunes(term, entity=None, limit=50)
        else:
            results = await search_itunes(term, entity_map[type_], limit=50)

        if results and results.get("resultCount", 0):
            await set_cached(cache_key, "search", {"type": type_, "term": term, "data": results})
            await status_msg.delete()
            await send_search_page(bot, message.chat.id, search_id, 1)
        else:
            await status_msg.edit_text(f"❌ هیچ نتیجه‌ای برای '{term}' یافت نشد.{FOOTER}")

async def send_search_page(bot: Bot, chat_id: int, search_id: str, page: int, edit_msg: Optional[Message] = None):
    cache_key = f"search:{search_id}"
    data = await get_cached(cache_key)
    if not data:
        text = f"❌ نتایج منقضی شده است.{FOOTER}"
        if edit_msg:
            await edit_msg.edit_text(text)
        else:
            await bot.send_message(chat_id, text)
        return

    type_ = data["type"]
    term = data["term"]
    items = data["data"]["results"]
    total = len(items)
    pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, pages))
    start = (page - 1) * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = items[start:end]

    markup = InlineKeyboardMarkup()
    header = f"📋 *نتایج جستجو {type_fa.get(type_, '')} برای: {term}*\nتعداد: {total}"

    btn_row = 1
    for item in page_items:
        if type_ == "all":
            wrapper = item.get("wrapperType")
            if wrapper == "artist":
                btn_text = f"🎤 {item.get('artistName', '?')[:45]}"
                callback = f"artist:{item['artistId']}:1"
            elif wrapper == "collection":
                btn_text = f"📀 {item.get('collectionName', '?')[:45]}"
                callback = f"album:{item['collectionId']}:1"
            elif wrapper == "track":
                btn_text = f"🎵 {item.get('trackName', '?')[:45]}"
                callback = f"track:{item['trackId']}"
            else:
                continue
        else:
            if type_ == "artist":
                btn_text = f"🎤 {item.get('artistName', '?')[:45]}"
                callback = f"artist:{item['artistId']}:1"
            elif type_ == "album":
                btn_text = f"📀 {item.get('collectionName', '?')[:45]}"
                callback = f"album:{item['collectionId']}:1"
            else:  # track
                btn_text = f"🎵 {item.get('trackName', '?')[:45]}"
                callback = f"track:{item['trackId']}"
        markup.add(InlineKeyboardButton(text=btn_text, callback_data=callback), row=btn_row)
        btn_row += 1

    if pages > 1:
        pag_row = create_pagination_row(f"page:search:{search_id}", page, pages)
        for btn in pag_row:
            markup.add(btn, row=btn_row)
        btn_row += 1
    markup.add(InlineKeyboardButton(text="🔍 جستجوی جدید", callback_data="new_search"), row=btn_row)

    text = header + FOOTER
    if edit_msg:
        await edit_msg.edit_text(text, components=markup)
    else:
        await bot.send_message(chat_id, text, components=markup)

@bot.on_callback_query()
async def on_callback(callback: CallbackQuery):
    data = callback.data
    chat_id = callback.message.chat.id
    logger.info(f"Callback: {data}")

    if data == "ignore":
        return
    if data == "new_search":
        await callback.answer()
        await callback.message.reply(f"🔍 عبارت جستجو را با `/search` ارسال کنید.{FOOTER}")
        return

    parts = data.split(":")
    if data.startswith("page:search:"):
        search_id = parts[2]
        page = int(parts[3])
        await send_search_page(bot, chat_id, search_id, page, callback.message)
    elif data.startswith("artist:"):
        artist_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        await show_artist(bot, chat_id, artist_id, page, callback.message)
    elif data.startswith("album:"):
        album_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        await show_album(bot, chat_id, album_id, page, callback.message)
    elif data.startswith("track:"):
        track_id = int(parts[1])
        await show_track(bot, chat_id, track_id, callback.message)
    elif data.startswith("download:"):
        track_id = int(parts[1])
        status_msg = await bot.send_message(chat_id, "⏳ شروع دانلود...")
        await send_cached_or_download(bot, chat_id, track_id, status_msg)
    elif data.startswith("preview:"):
        track_id = int(parts[1])
        await send_voice_preview(bot, chat_id, track_id)
    elif data.startswith("recrawl:"):
        t = parts[1]
        id_ = int(parts[2])
        if t == "artist":
            await delete_cached(f"artist:{id_}")
            await delete_cached(f"artist_albums:{id_}")
            await show_artist(bot, chat_id, id_, 1, callback.message)
        elif t == "album":
            await delete_cached(f"album:{id_}")
            await delete_cached(f"album_tracks:{id_}")
            await show_album(bot, chat_id, id_, 1, callback.message)
        elif t == "track":
            await delete_cached(f"track:{id_}")
            await show_track(bot, chat_id, id_, callback.message)
    elif data.startswith("artist_tracks:"):
        artist_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 1
        artist_data = await get_artist(artist_id)
        if not artist_data:
            await callback.message.reply(f"❌ هنرمند یافت نشد.{FOOTER}")
            return
        artist_name = artist_data["results"][0].get("artistName", "")
        search_id = generate_search_hash("track", artist_name)
        results = await search_itunes(artist_name, entity="musicTrack", limit=50)
        if results and results.get("resultCount", 0):
            await set_cached(f"search:{search_id}", "search",
                             {"type": "track", "term": artist_name, "data": results})
            await send_search_page(bot, chat_id, search_id, page, callback.message)
        else:
            await callback.message.reply(f"❌ هیچ آهنگی برای این هنرمند یافت نشد.{FOOTER}")

    await callback.answer()

async def show_artist(bot: Bot, chat_id: int, artist_id: int, page: int, edit_msg: Optional[Message] = None):
    status = await bot.send_message(chat_id, f"🔄 *در حال پردازش هنرمند...*{FOOTER}")
    data = await get_artist(artist_id, status)
    if not data or not data.get("results"):
        await status.edit_text(f"❌ هنرمند یافت نشد.{FOOTER}")
        return
    artist = data["results"][0]
    text = f"*🎤 هنرمند:* {artist.get('artistName', 'نامشخص')}\n"
    text += f"*🎭 سبک:* {artist.get('primaryGenreName', 'نامشخص')}\n"
    if artist.get("artistLinkUrl"):
        text += f"*🔗 لینک:* [آیتونز]({artist['artistLinkUrl']})\n"

    albums_cache = await get_cached(f"artist_albums:{artist_id}")
    if not albums_cache:
        await crawl_artist_albums(artist_id, status)
        albums_cache = await get_cached(f"artist_albums:{artist_id}")

    albums = []
    if albums_cache and "albums" in albums_cache:
        for aid in albums_cache["albums"]:
            album_data = await get_cached(f"album:{aid}")
            if album_data and album_data.get("results"):
                albums.append(album_data["results"][0])

    markup = InlineKeyboardMarkup()
    row = 1
    if albums:
        total = len(albums)
        pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, pages))
        start = (page - 1) * ITEMS_PER_PAGE
        page_items = albums[start:start + ITEMS_PER_PAGE]
        text += f"\n*📀 آلبوم‌ها ({total}):*\n"
        for alb in page_items:
            btn_text = f"📀 {alb.get('collectionName', '?')[:45]}"
            markup.add(InlineKeyboardButton(text=btn_text, callback_data=f"album:{alb['collectionId']}:1"), row=row)
            row += 1
        if pages > 1:
            pag_row = create_pagination_row(f"artist:{artist_id}", page, pages)
            for btn in pag_row:
                markup.add(btn, row=row)
            row += 1
    markup.add(InlineKeyboardButton(text="🎵 آهنگ‌های هنرمند", callback_data=f"artist_tracks:{artist_id}:1"), row=row)
    markup.add(InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data=f"recrawl:artist:{artist_id}"), row=row+1)
    markup.add(InlineKeyboardButton(text="🔍 جستجوی جدید", callback_data="new_search"), row=row+2)

    await status.delete()
    if edit_msg:
        await edit_msg.edit_text(text + FOOTER, components=markup)
    else:
        await bot.send_message(chat_id, text + FOOTER, components=markup)

async def show_album(bot: Bot, chat_id: int, album_id: int, page: int, edit_msg: Optional[Message] = None):
    status = await bot.send_message(chat_id, f"🔄 *در حال پردازش آلبوم...*{FOOTER}")
    data = await get_album(album_id, status)
    if not data or not data.get("results"):
        await status.edit_text(f"❌ آلبوم یافت نشد.{FOOTER}")
        return
    album = data["results"][0]
    release = album.get('releaseDate', 'نامشخص')[:10]
    text = f"*📀 آلبوم:* {album.get('collectionName', 'نامشخص')}\n"
    text += f"*🎤 هنرمند:* {album.get('artistName', 'نامشخص')}\n"
    text += f"*📅 انتشار:* {release}\n🎭 سبک: {album.get('primaryGenreName', 'نامشخص')}\n"
    if album.get("collectionViewUrl"):
        text += f"*🔗 لینک:* [آیتونز]({album['collectionViewUrl']})\n"

    tracks_cache = await get_cached(f"album_tracks:{album_id}")
    if not tracks_cache:
        await crawl_album_tracks(album_id, status)
        tracks_cache = await get_cached(f"album_tracks:{album_id}")

    tracks = []
    if tracks_cache and "tracks" in tracks_cache:
        for tid in tracks_cache["tracks"]:
            track_data = await get_cached(f"track:{tid}")
            if track_data and track_data.get("results"):
                tracks.append(track_data["results"][0])

    markup = InlineKeyboardMarkup()
    row = 1
    if tracks:
        total = len(tracks)
        pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, pages))
        start = (page - 1) * ITEMS_PER_PAGE
        page_items = tracks[start:start + ITEMS_PER_PAGE]
        text += f"\n*🎵 قطعات ({total}):*\n"
        for i, tr in enumerate(page_items, start+1):
            dur = format_duration(tr.get('trackTimeMillis', 0))
            text += f"`{i}.` {tr.get('trackName', '?')} ({dur})\n"
        for tr in page_items:
            btn_text = f"🎵 {tr.get('trackName', '?')[:40]}"
            markup.add(InlineKeyboardButton(text=btn_text, callback_data=f"track:{tr['trackId']}"), row=row)
            row += 1
        if pages > 1:
            pag_row = create_pagination_row(f"album:{album_id}", page, pages)
            for btn in pag_row:
                markup.add(btn, row=row)
            row += 1
    if album.get("artistId"):
        markup.add(InlineKeyboardButton(text="🎤 مشاهده هنرمند", callback_data=f"artist:{album['artistId']}:1"), row=row)
        row += 1
    markup.add(InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data=f"recrawl:album:{album_id}"), row=row)
    markup.add(InlineKeyboardButton(text="🔍 جستجوی جدید", callback_data="new_search"), row=row+1)

    await status.delete()
    artwork = get_high_res_artwork(album.get("artworkUrl100"))
    sent = False
    if artwork:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(artwork) as resp:
                    if resp.status == 200:
                        img = InputFile(await resp.read(), file_name="cover.jpg")
                        if edit_msg:
                            await edit_msg.delete()
                        await bot.send_photo(chat_id, img, caption=text + FOOTER, components=markup)
                        sent = True
        except Exception as e:
            logger.error(f"Album cover error: {e}")
    if not sent:
        if edit_msg:
            await edit_msg.edit_text(text + FOOTER, components=markup)
        else:
            await bot.send_message(chat_id, text + FOOTER, components=markup)

async def show_track(bot: Bot, chat_id: int, track_id: int, edit_msg: Optional[Message] = None):
    status = await bot.send_message(chat_id, f"🔄 *در حال بارگذاری آهنگ...*{FOOTER}")
    data = await get_track(track_id, status)
    if not data or not data.get("results"):
        await status.edit_text(f"❌ آهنگ یافت نشد.{FOOTER}")
        return
    track = data["results"][0]
    duration = format_duration(track.get('trackTimeMillis', 0))
    release = track.get('releaseDate', 'نامشخص')[:10]
    text = f"*🎵 آهنگ:* {track.get('trackName', 'نامشخص')}\n"
    text += f"*🎤 هنرمند:* {track.get('artistName', 'نامشخص')}\n"
    text += f"*📀 آلبوم:* {track.get('collectionName', 'نامشخص')}\n"
    text += f"*⏱️ مدت:* {duration}\n🎭 سبک: {track.get('primaryGenreName', 'نامشخص')}\n"
    text += f"*📅 انتشار:* {release}\n"
    if track.get("trackViewUrl"):
        text += f"*🔗 لینک:* [آیتونز]({track['trackViewUrl']})\n"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text="⬇️ دانلود (320 kbps)", callback_data=f"download:{track_id}"), row=1)
    if track.get("previewUrl"):
        markup.add(InlineKeyboardButton(text="🎧 پیش‌نمایش", callback_data=f"preview:{track_id}"), row=2)
    row = 3
    if track.get('collectionId'):
        markup.add(InlineKeyboardButton(text="📀 مشاهده آلبوم", callback_data=f"album:{track['collectionId']}:1"), row=row)
        row += 1
    if track.get('artistId'):
        markup.add(InlineKeyboardButton(text="🎤 مشاهده هنرمند", callback_data=f"artist:{track['artistId']}:1"), row=row)
        row += 1
    markup.add(InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data=f"recrawl:track:{track_id}"), row=row)
    markup.add(InlineKeyboardButton(text="🔍 جستجوی جدید", callback_data="new_search"), row=row+1)

    await status.delete()
    artwork = get_high_res_artwork(track.get("artworkUrl100"))
    sent = False
    if artwork:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(artwork) as resp:
                    if resp.status == 200:
                        img = InputFile(await resp.read(), file_name="cover.jpg")
                        if edit_msg:
                            await edit_msg.delete()
                        await bot.send_photo(chat_id, img, caption=text + FOOTER, components=markup)
                        sent = True
        except Exception as e:
            logger.error(f"Track cover error: {e}")
    if not sent:
        if edit_msg:
            await edit_msg.edit_text(text + FOOTER, components=markup)
        else:
            await bot.send_message(chat_id, text + FOOTER, components=markup)

type_fa = {"artist": "هنرمند", "album": "آلبوم", "track": "آهنگ", "all": "همه"}

if __name__ == "__main__":
    logger.info(f"🎵 {BOT_NAME} starting with balethon...")
    bot.run()
