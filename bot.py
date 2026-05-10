"""
bale_music_bot.py
Abraava Music Bot with advanced 8‑method YouTube Music downloader, metadata tagging, and caching.
"""

import logging
import json
import time
import asyncio
import hashlib
import os
import aiohttp
import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any, List

from ytmusicapi import YTMusic
from bale import Bot, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, error

# Import the 8-method downloader (Ensure this file exists in your project)
from youtube_downloader import download_audio

# ---------- Configuration ----------
ITUNES_BASE_URL = "https://itunes.apple.com"
DB_PATH = Path("cache.db")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "1011430416:5JY8CU9nGwYtVz0ahfDEIkJyCkVTUCAhLXQ")
DB_CHANNEL_ID = os.environ.get("DB_CHANNEL_ID", None)
ITEMS_PER_PAGE = 10

BOT_NAME = "ابرآوا"
FOOTER = "\n\n@abraava_bot\n@abraava"

# ---------- Logging Setup ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AbraavaBot")

# ---------- Async SQLite Database ----------
class Database:
    @staticmethod
    async def init():
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
        logger.info("Database initialized successfully.")

    @staticmethod
    async def get_cache(cache_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT data FROM cache WHERE id = ?", (cache_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return json.loads(row[0])
        return None

    @staticmethod
    async def set_cache(cache_id: str, type_: str, data: Dict[str, Any]):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT OR REPLACE INTO cache (id, type, data, last_updated)
                VALUES (?, ?, ?, ?)
            """, (cache_id, type_, json.dumps(data), int(time.time())))
            await db.commit()

    @staticmethod
    async def delete_cache(cache_id: str):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM cache WHERE id = ?", (cache_id,))
            await db.commit()

    @staticmethod
    async def is_cached(cache_id: str) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT 1 FROM cache WHERE id = ?", (cache_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def get_audio_cache(track_id: int) -> Optional[int]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT channel_message_id FROM audio_cache WHERE track_id = ?", (track_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    @staticmethod
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

# ---------- iTunes API & Crawlers ----------
class iTunes:
    @staticmethod
    async def fetch(endpoint: str, params: dict) -> Optional[Dict[str, Any]]:
        session = await HttpClient.get_session()
        url = f"{ITUNES_BASE_URL}/{endpoint}"
        try:
            async with session.get(url, params=params, ssl=False) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON from {url}")
                else:
                    logger.warning(f"iTunes API returned {resp.status} for {url}")
        except Exception as e:
            logger.error(f"Error fetching from iTunes API: {e}")
        return None

    @staticmethod
    async def search(term: str, entity: Optional[str] = None, limit: int = 50) -> Optional[Dict[str, Any]]:
        params = {"term": term, "media": "music", "limit": limit, "country": "US"}
        if entity:
            params["entity"] = entity
        return await iTunes.fetch("search", params)

    @staticmethod
    async def lookup(item_id: int, entity: Optional[str] = None) -> Optional[Dict[str, Any]]:
        params = {"id": item_id, "country": "US"}
        if entity:
            params["entity"] = entity
        return await iTunes.fetch("lookup", params)

class Crawler:
    @staticmethod
    async def get_artist(artist_id: int) -> Optional[Dict[str, Any]]:
        cache_id = f"artist:{artist_id}"
        cached = await Database.get_cache(cache_id)
        if cached: return cached
        data = await iTunes.lookup(artist_id)
        if data and data.get("results"):
            await Database.set_cache(cache_id, "artist", data)
            return data
        return None

    @staticmethod
    async def get_artist_albums(artist_id: int) -> List[int]:
        cache_id = f"artist_albums:{artist_id}"
        cached = await Database.get_cache(cache_id)
        if cached: return cached.get("albums", [])
        data = await iTunes.lookup(artist_id, "album")
        albums = []
        if data and data.get("resultCount", 0) > 0:
            for item in data["results"]:
                if item.get("wrapperType") == "collection":
                    albums.append(item["collectionId"])
            await Database.set_cache(cache_id, "artist_albums", {"albums": albums})
        return albums

    @staticmethod
    async def get_album(album_id: int) -> Optional[Dict[str, Any]]:
        cache_id = f"album:{album_id}"
        cached = await Database.get_cache(cache_id)
        if cached: return cached
        data = await iTunes.lookup(album_id)
        if data and data.get("results"):
            await Database.set_cache(cache_id, "album", data)
            return data
        return None

    @staticmethod
    async def get_album_tracks(album_id: int) -> List[int]:
        cache_id = f"album_tracks:{album_id}"
        cached = await Database.get_cache(cache_id)
        if cached: return cached.get("tracks", [])
        data = await iTunes.lookup(album_id, "song")
        tracks = []
        if data and data.get("resultCount", 0) > 0:
            for item in data["results"]:
                if item.get("wrapperType") == "track":
                    tracks.append(item["trackId"])
            await Database.set_cache(cache_id, "album_tracks", {"tracks": tracks})
        return tracks

    @staticmethod
    async def get_track(track_id: int) -> Optional[Dict[str, Any]]:
        cache_id = f"track:{track_id}"
        cached = await Database.get_cache(cache_id)
        if cached: return cached
        data = await iTunes.lookup(track_id)
        if data and data.get("results"):
            await Database.set_cache(cache_id, "track", data)
            return data
        return None

# ---------- YouTube Music Helper ----------
class YTMusicHelper:
    _yt: Optional[YTMusic] = None

    @classmethod
    def get_instance(cls) -> YTMusic:
        if cls._yt None:
            cls._yt = YTMusic()
        return cls._yt

    @classmethod
    async def search_track(cls, query: str) -> Optional[str]:
        try:
            yt = cls.get_instance()
            results = yt.search(query, filter="songs", limit=1)
            if results and len(results) > 0:
                return results[0].get("videoId")
        except Exception as e:
            logger.error(f"YTMusic search error: {e}")
        return None

# ---------- Utilities ----------
def format_duration(milliseconds: int) -> str:
    if not milliseconds: return "نامشخص"
    minutes = milliseconds // 60000
    seconds = (milliseconds % 60000) // 1000
    return f"{minutes}:{seconds:02d}"

def get_high_res_artwork(url: str, size: int = 600) -> str:
    return url.replace("100x100bb", f"{size}x{size}bb") if url else ""

def generate_search_hash(type_: str, term: str) -> str:
    return hashlib.md5(f"{type_}:{term}".encode()).hexdigest()[:10]

def create_pagination_row(callback_prefix: str, current_page: int, total_pages: int) -> List[InlineKeyboardButton]:
    row = []
    if current_page > 1:
        row.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"{callback_prefix}:{current_page - 1}"))
    row.append(InlineKeyboardButton(text=f"صفحه {current_page} از {total_pages}", callback_data="ignore"))
    if current_page < total_pages:
        row.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"{callback_prefix}:{current_page + 1}"))
    return row

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
    except Exception as e:
        logger.error(f"Failed to tag MP3 {file_path}: {e}")

# ---------- Bale Bot Handlers ----------
bot = Bot(token=BOT_TOKEN)

@bot.event
async def on_ready():
    logger.info(f"{bot.user.username} is ready!")
    await bot.delete_webhook()
    await Database.init()

@bot.event
async def on_message(message: Message):
    if not message.content: return
    msg_text = message.content

    if message.chat.type in ["group", "supergroup", "channel"]:
        bot_mention = f"@{bot.user.username}"
        if bot_mention not in msg_text: return
        msg_text = msg_text.replace(bot_mention, "").strip()

    if msg_text.startswith("/start"):
        await message.reply(f"🎵 *به ربات {BOT_NAME} خوش آمدید!*\nبرای جستجو از `/search <متن>` استفاده کنید.{FOOTER}")
    elif msg_text.startswith("/search"):
        parts = msg_text.split(" ", 1)
        if len(parts) < 2:
            await message.reply(f"❌ *عبارت جستجو را وارد کنید.*{FOOTER}")
            return
        
        query = parts[1].strip()
        type_ = "all"
        if ":" in query:
            t, q = query.split(":", 1)
            if t.lower() in ["artist", "album", "track"]:
                type_ = t.lower()
                query = q.strip()
        
        await process_search(message, type_, query)

async def process_search(message: Message, type_: str, term: str):
    status_msg = await message.reply(f"🔍 *در حال جستجو...*{FOOTER}")
    search_id = generate_search_hash(type_, term)
    cache_key = f"search:{search_id}"

    entity_map = {"artist": "musicArtist", "album": "album", "track": "musicTrack"}
    entity = entity_map.get(type_)
    results = await iTunes.search(term, entity=entity, limit=50)

    if results and results.get("resultCount", 0) > 0:
        await Database.set_cache(cache_key, "search", {"type": type_, "term": term, "data": results})
        await status_msg.delete()
        await send_search_page(message.chat.id, search_id, 1)
    else:
        await status_msg.edit(f"❌ *هیچ نتیجه‌ای یافت نشد.*{FOOTER}")

async def send_search_page(chat_id: int, search_id: str, page: int, message_to_edit: Message = None):
    cache_key = f"search:{search_id}"
    cache_data = await Database.get_cache(cache_key)
    if not cache_data:
        text = f"❌ خطایی رخ داد یا سشن منقضی شده است.{FOOTER}"
        if message_to_edit: await message_to_edit.edit(text)
        else: await bot.send_message(chat_id, text)
        return

    type_ = cache_data["type"]
    term = cache_data["term"]
    results_list = cache_data["data"]["results"]
    
    total_pages = (len(results_list) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * ITEMS_PER_PAGE
    page_items = results_list[start_idx:start_idx + ITEMS_PER_PAGE]

    markup = InlineKeyboardMarkup()
    for i, item in enumerate(page_items, 1):
        wrapper = item.get("wrapperType", item.get("kind"))
        if wrapper in ["artist", "musicArtist"] or type_ == "artist":
            btn_text = f"🎤 {item.get('artistName', 'نامشخص')}"
            cb_data = f"artist:{item.get('artistId')}:1"
        elif wrapper in ["collection", "album"] or type_ == "album":
            btn_text = f"📀 {item.get('collectionName', 'نامشخص')[:40]}"
            cb_data = f"album:{item.get('collectionId')}:1"
        else:
            btn_text = f"🎵 {item.get('trackName', 'نامشخص')[:40]}"
            cb_data = f"track:{item.get('trackId')}"
        markup.add(InlineKeyboardButton(text=btn_text, callback_data=cb_data), row=i)

    row_idx = len(page_items) + 1
    if total_pages > 1:
        for btn in create_pagination_row(f"page:search:{search_id}", page, total_pages):
            markup.add(btn, row=row_idx)
        row_idx += 1

    # Filter buttons for "all" search
    if type_ == "all":
        markup.add(InlineKeyboardButton(text="فیلتر آرتیست", callback_data=f"filter:{term}:artist"), row=row_idx)
        markup.add(InlineKeyboardButton(text="فیلتر آلبوم", callback_data=f"filter:{term}:album"), row=row_idx)
        markup.add(InlineKeyboardButton(text="فیلتر آهنگ", callback_data=f"filter:{term}:track"), row=row_idx)
        row_idx += 1

    text = f"📋 *نتایج جستجو برای: {term}*\nتعداد کل: {len(results_list)}{FOOTER}"
    if message_to_edit:
        await message_to_edit.edit(text, components=markup)
    else:
        await bot.send_message(chat_id, text, components=markup)

@bot.event
async def on_callback(callback: CallbackQuery):
    data = callback.data
    chat_id = callback.message.chat.id
    if data == "ignore": return

    try:
        parts = data.split(":")
        action = parts[0]

        if action == "page" and parts[1] == "search":
            await send_search_page(chat_id, parts[2], int(parts[3]), callback.message)
        elif action == "filter":
            await process_search(callback.message, type_=parts[2], term=parts[1])
        elif action == "artist":
            await show_artist(chat_id, int(parts[1]), int(parts[2]), callback.message)
        elif action == "album":
            await show_album(chat_id, int(parts[1]), int(parts[2]), callback.message)
        elif action == "track":
            await show_track(chat_id, int(parts[1]), callback.message)
        elif action == "download":
            await handle_download(chat_id, int(parts[1]))
    except Exception as e:
        logger.error(f"Callback error {data}: {e}")

async def show_artist(chat_id: int, artist_id: int, page: int, msg: Message):
    data = await Crawler.get_artist(artist_id)
    if not data: return await msg.edit(f"❌ یافت نشد.{FOOTER}")
    
    artist = data["results"][0]
    albums_ids = await Crawler.get_artist_albums(artist_id)
    
    text = f"*🎤 هنرمند:* {artist.get('artistName')}{FOOTER}"
    markup = InlineKeyboardMarkup()
    
    if albums_ids:
        total_pages = (len(albums_ids) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * ITEMS_PER_PAGE
        
        for i, album_id in enumerate(albums_ids[start_idx:start_idx+ITEMS_PER_PAGE], 1):
            al_data = await Crawler.get_album(album_id)
            if al_data:
                name = al_data["results"][0].get('collectionName', 'نامشخص')[:40]
                markup.add(InlineKeyboardButton(text=f"📀 {name}", callback_data=f"album:{album_id}:1"), row=i)
        
        row = len(albums_ids[start_idx:start_idx+ITEMS_PER_PAGE]) + 1
        if total_pages > 1:
            for btn in create_pagination_row(f"artist:{artist_id}", page, total_pages):
                markup.add(btn, row=row)
    
    await msg.edit(text, components=markup)

async def show_album(chat_id: int, album_id: int, page: int, msg: Message):
    data = await Crawler.get_album(album_id)
    if not data: return await msg.edit(f"❌ یافت نشد.{FOOTER}")
    
    album = data["results"][0]
    tracks_ids = await Crawler.get_album_tracks(album_id)
    
    text = f"*📀 آلبوم:* {album.get('collectionName')}\n*🎤 هنرمند:* {album.get('artistName')}{FOOTER}"
    markup = InlineKeyboardMarkup()
    
    if tracks_ids:
        total_pages = (len(tracks_ids) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * ITEMS_PER_PAGE
        
        for i, track_id in enumerate(tracks_ids[start_idx:start_idx+ITEMS_PER_PAGE], 1):
            tr_data = await Crawler.get_track(track_id)
            if tr_data:
                name = tr_data["results"][0].get('trackName', 'نامشخص')[:40]
                markup.add(InlineKeyboardButton(text=f"🎵 {name}", callback_data=f"track:{track_id}"), row=i)
        
        row = len(tracks_ids[start_idx:start_idx+ITEMS_PER_PAGE]) + 1
        if total_pages > 1:
            for btn in create_pagination_row(f"album:{album_id}", page, total_pages):
                markup.add(btn, row=row)
        
        if album.get('artistId'):
            markup.add(InlineKeyboardButton(text="🔙 بازگشت به هنرمند", callback_data=f"artist:{album['artistId']}:1"), row=row+1)

    await msg.edit(text, components=markup)

async def show_track(chat_id: int, track_id: int, msg: Message):
    data = await Crawler.get_track(track_id)
    if not data: return await msg.edit(f"❌ یافت نشد.{FOOTER}")
    
    track = data["results"][0]
    text = f"*🎵 آهنگ:* {track.get('trackName')}\n*🎤 هنرمند:* {track.get('artistName')}{FOOTER}"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text="⬇️ دانلود (320 kbps)", callback_data=f"download:{track_id}"), row=1)
    
    row = 2
    if track.get('collectionId'):
        markup.add(InlineKeyboardButton(text="📀 مشاهده آلبوم", callback_data=f"album:{track['collectionId']}:1"), row=row)
        row += 1
    if track.get('artistId'):
        markup.add(InlineKeyboardButton(text="🎤 مشاهده هنرمند", callback_data=f"artist:{track['artistId']}:1"), row=row)

    await msg.edit(text, components=markup)

async def handle_download(chat_id: int, track_id: int):
    status_msg = await bot.send_message(chat_id, f"⏳ *در حال آماده‌سازی دانلود...*{FOOTER}")
    
    cached_msg_id = await Database.get_audio_cache(track_id)
    if cached_msg_id and DB_CHANNEL_ID:
        try:
            await bot.forward_message(chat_id, from_chat_id=DB_CHANNEL_ID, message_id=cached_msg_id)
            return await status_msg.edit(f"✅ ارسال از کش دیتابیس.{FOOTER}")
        except: pass

    data = await Crawler.get_track(track_id)
    if not data: return await status_msg.edit(f"❌ خطا در اطلاعات آهنگ.{FOOTER}")
    
    track = data["results"][0]
    query = f"{track.get('trackName')} {track.get('artistName')}"
    
    video_id = await YTMusicHelper.search_track(query)
    if not video_id: return await status_msg.edit(f"❌ در یوتیوب یافت نشد.{FOOTER}")
    
    await status_msg.edit(f"⏳ در حال دانلود سورس اورجینال...{FOOTER}")
    
    try:
        mp3_path = await asyncio.get_event_loop().run_in_executor(None, download_audio, f"https://music.youtube.com/watch?v={video_id}")
        if not mp3_path: return await status_msg.edit(f"❌ دانلود با شکست مواجه شد.{FOOTER}")

        # Fetch Cover
        cover_url = get_high_res_artwork(track.get("artworkUrl100"))
        cover_bytes = None
        if cover_url:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(cover_url) as resp:
                    if resp.status == 200: cover_bytes = await resp.read()

        await asyncio.get_event_loop().run_in_executor(None, tag_mp3, mp3_path, track.get('trackName'), track.get('artistName'), track.get('collectionName', ''), cover_bytes)

        caption = f"🎵 {track.get('trackName')}\n🎤 {track.get('artistName')}{FOOTER}"
        
        # FIXED: Pass bytes directly to InputFile to prevent 'invalid url' error
        with open(mp3_path, "rb") as f:
            audio_data = f.read()
        
        audio_input = InputFile(audio_data, file_name=f"{track.get('trackName')}.mp3")

        if DB_CHANNEL_ID:
            try:
                db_msg = await bot.send_audio(int(DB_CHANNEL_ID), audio=audio_input, caption=caption)
                await Database.set_audio_cache(track_id, db_msg.message_id)
                await bot.forward_message(chat_id, from_chat_id=DB_CHANNEL_ID, message_id=db_msg.message_id)
            except Exception as e:
                logger.error(f"DB Channel upload failed: {e}")
                # Re-create InputFile buffer for direct send
                audio_input = InputFile(audio_data, file_name=f"{track.get('trackName')}.mp3")
                await bot.send_audio(chat_id, audio=audio_input, caption=caption)
        else:
            await bot.send_audio(chat_id, audio=audio_input, caption=caption)

        await status_msg.delete()
        mp3_path.unlink(missing_ok=True)
        
    except Exception as e:
        logger.exception("Download error")
        await status_msg.edit(f"❌ خطا: {e}{FOOTER}")

if __name__ == "__main__":
    bot.run()
