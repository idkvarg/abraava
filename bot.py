import asyncio
import os
import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager

import aiosqlite
import yt_dlp
from balethon import Client
from balethon.conditions import command, text, private, chat
from balethon.objects import InlineKeyboard, InlineKeyboardButton, CallbackQuery, Message
from balethon.enums import ChatType

# ==========================  CONFIGURATION  ==========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BALE_BOT_TOKEN")
CACHE_CHANNEL_ID = int(os.getenv("CACHE_CHANNEL_ID", "-1000000000000"))
BROADCAST_CHANNEL_ID = int(os.getenv("BROADCAST_CHANNEL_ID", "0"))  # optional
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))  # for admin commands
SOUNDCLOUD_QUALITY = os.getenv("SOUNDCLOUD_QUALITY", "192")  # 128, 192, 320
TEMP_DIR = Path("temp_soundcloud")
TEMP_DIR.mkdir(exist_ok=True)
DB_PATH = "bot_cache.db"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

bot = Client(BOT_TOKEN)
bot_username: Optional[str] = None

# ==========================  DATABASE (async)  ==========================
class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @asynccontextmanager
    async def get_connection(self):
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def init(self):
        async with self.get_connection() as conn:
            # tracks table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tracks (
                    uuid TEXT PRIMARY KEY,
                    title TEXT,
                    uploader TEXT,
                    genre TEXT,
                    upload_date TEXT,
                    webpage_url TEXT UNIQUE,
                    thumbnail TEXT,
                    duration TEXT,
                    file_id TEXT,
                    cache_msg_id TEXT
                )
            ''')
            # users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    quality TEXT DEFAULT '192'
                )
            ''')
            # pending downloads (to avoid concurrency)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS downloads (
                    chat_id INTEGER,
                    track_uuid TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, track_uuid)
                )
            ''')
            await conn.commit()

    async def add_user(self, chat_id: int, quality: str = None):
        async with self.get_connection() as conn:
            if quality:
                await conn.execute('INSERT OR REPLACE INTO users (chat_id, quality) VALUES (?, ?)', (chat_id, quality))
            else:
                await conn.execute('INSERT OR IGNORE INTO users (chat_id) VALUES (?)', (chat_id,))
            await conn.commit()

    async def get_user_quality(self, chat_id: int) -> str:
        async with self.get_connection() as conn:
            cursor = await conn.execute('SELECT quality FROM users WHERE chat_id = ?', (chat_id,))
            row = await cursor.fetchone()
            return row['quality'] if row else SOUNDCLOUD_QUALITY

    async def set_user_quality(self, chat_id: int, quality: str):
        async with self.get_connection() as conn:
            await conn.execute('UPDATE users SET quality = ? WHERE chat_id = ?', (quality, chat_id))
            await conn.commit()

    async def get_track_by_url(self, url: str) -> Optional[Dict]:
        async with self.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM tracks WHERE webpage_url = ?', (url,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_track_by_uuid(self, uuid: str) -> Optional[Dict]:
        async with self.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM tracks WHERE uuid = ?', (uuid,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def save_track(self, track: Dict):
        placeholders = ','.join(['?'] * len(track))
        columns = ','.join(track.keys())
        async with self.get_connection() as conn:
            await conn.execute(f'INSERT OR REPLACE INTO tracks ({columns}) VALUES ({placeholders})', tuple(track.values()))
            await conn.commit()

    async def set_cache_info(self, uuid: str, file_id: str = None, cache_msg_id: str = None):
        async with self.get_connection() as conn:
            if file_id:
                await conn.execute('UPDATE tracks SET file_id = ? WHERE uuid = ?', (file_id, uuid))
            if cache_msg_id:
                await conn.execute('UPDATE tracks SET cache_msg_id = ? WHERE uuid = ?', (cache_msg_id, uuid))
            await conn.commit()

    async def get_all_users(self) -> List[int]:
        async with self.get_connection() as conn:
            cursor = await conn.execute('SELECT chat_id FROM users')
            rows = await cursor.fetchall()
            return [row['chat_id'] for row in rows]

    async def is_downloading(self, chat_id: int, track_uuid: str) -> bool:
        async with self.get_connection() as conn:
            cursor = await conn.execute('SELECT 1 FROM downloads WHERE chat_id = ? AND track_uuid = ?', (chat_id, track_uuid))
            row = await cursor.fetchone()
            return row is not None

    async def add_downloading(self, chat_id: int, track_uuid: str):
        async with self.get_connection() as conn:
            await conn.execute('INSERT OR IGNORE INTO downloads (chat_id, track_uuid) VALUES (?, ?)', (chat_id, track_uuid))
            await conn.commit()

    async def remove_downloading(self, chat_id: int, track_uuid: str):
        async with self.get_connection() as conn:
            await conn.execute('DELETE FROM downloads WHERE chat_id = ? AND track_uuid = ?', (chat_id, track_uuid))
            await conn.commit()

    async def cleanup_old_downloads(self, max_age_seconds: int = 300):
        async with self.get_connection() as conn:
            await conn.execute('DELETE FROM downloads WHERE started_at < datetime("now", ?)', (f'-{max_age_seconds} seconds',))
            await conn.commit()

db = Database(DB_PATH)

# ==========================  SOUNDCLOUD HELPERS  ==========================
def extract_urls(text: str) -> List[str]:
    """Extract SoundCloud URLs from text."""
    pattern = r'(https?://(?:www\.)?soundcloud\.com/[^\s]+)'
    return re.findall(pattern, text)

def is_playlist_url(url: str) -> bool:
    """Check if URL points to a playlist/set."""
    return '/sets/' in url or 'playlists' in url

async def run_async(func, *args, **kwargs):
    """Run synchronous function in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

def get_info_sync(url: str) -> Dict:
    """Extract track/playlist info (synchronous)."""
    ydl_opts = {'quiet': True, 'extract_flat': False, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_track_sync(url: str, quality: str, output_template: str) -> Tuple[str, Dict]:
    """Download audio as MP3 (synchronous). Returns (filepath, info)."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': quality,
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = output_template.replace('%(id)s', info['id']).replace('.%(ext)s', '.mp3')
        return filepath, info

async def search_soundcloud(query: str, max_results: int = 10) -> List[Dict]:
    """Search SoundCloud using yt-dlp's scsearch."""
    ydl_opts = {'quiet': True, 'extract_flat': True, 'no_warnings': True}
    results = []
    try:
        info = await run_async(get_info_sync, f'scsearch{max_results}:{query}')
        entries = info.get('entries', [])
        for e in entries:
            results.append({
                'id': e.get('id'),
                'title': e.get('title', 'Unknown'),
                'uploader': e.get('uploader', 'Unknown'),
                'webpage_url': e.get('webpage_url', '').split('?')[0],
                'thumbnail': e.get('thumbnail', ''),
                'duration': e.get('duration', 0),
                'is_playlist': False,
            })
    except Exception as e:
        logger.error(f"Search error: {e}")
    return results

def format_duration(seconds: int) -> str:
    if not seconds:
        return 'N/A'
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}:{secs:02d}"

def build_track_caption(track: Dict, username: str) -> str:
    return (
        f"🎧 *{track.get('title', 'Unknown')}*\n"
        f"🎤 Artist: *{track.get('uploader', 'Unknown')}*\n"
        f"📅 Year: {track.get('upload_date', 'Unknown')}\n"
        f"🎸 Genre: {track.get('genre', 'Unknown')}\n"
        f"⏱ Duration: {track.get('duration', 'Unknown')}\n"
        f"🔗 [Original]({track.get('webpage_url', '#')})\n\n"
        f"🤖 @{username}"
    )

# ==========================  INLINE KEYBOARDS  ==========================
def get_track_keyboard(track_uuid: str) -> InlineKeyboard:
    return InlineKeyboard(
        InlineKeyboardButton("⬇️ Get Audio", callback_data=f"audio:{track_uuid}"),
        InlineKeyboardButton("ℹ️ More Info", callback_data=f"info:{track_uuid}")
    )

def get_settings_keyboard(current_quality: str) -> InlineKeyboard:
    qualities = ['128', '192', '320']
    buttons = []
    for q in qualities:
        text = f"{q} kbps {'✅' if q == current_quality else ''}"
        buttons.append(InlineKeyboardButton(text, callback_data=f"setqual:{q}"))
    return InlineKeyboard(*buttons, row_width=3)

def get_search_results_keyboard(results: List[Dict], page: int = 0, per_page: int = 5) -> InlineKeyboard:
    start = page * per_page
    end = start + per_page
    kb = []
    for item in results[start:end]:
        title = item['title'][:30] + ('...' if len(item['title']) > 30 else '')
        kb.append([InlineKeyboardButton(f"🎵 {title} — {item['uploader']}", callback_data=f"select:{item['webpage_url']}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"searchpage:{page-1}"))
    if end < len(results):
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"searchpage:{page+1}"))
    if nav_buttons:
        kb.append(nav_buttons)
    return InlineKeyboard(*[btn for row in kb for btn in row], row_width=1)

# ==========================  MEDIA HANDLER (CACHING)  ==========================
async def send_cached_audio(chat_id: int, track: Dict) -> bool:
    """Try to send audio using cached file_id or forward from channel."""
    if track.get('file_id'):
        try:
            await bot.send_audio(chat_id, track['file_id'], title=track['title'])
            return True
        except Exception as e:
            logger.warning(f"File ID expired for {track['uuid']}: {e}")
    if track.get('cache_msg_id'):
        try:
            await bot.forward_message(chat_id, CACHE_CHANNEL_ID, int(track['cache_msg_id']))
            return True
        except Exception as e:
            logger.warning(f"Forward failed for {track['uuid']}: {e}")
    return False

async def download_and_cache(track: Dict, quality: str, chat_id: int) -> Optional[str]:
    """Download track, upload to cache channel, store file_id, return file_id or None."""
    url = track['webpage_url']
    temp_file = TEMP_DIR / f"{track['uuid']}.mp3"
    try:
        logger.info(f"Downloading {url} with quality {quality}")
        filepath, info = await run_async(download_track_sync, url, quality, str(TEMP_DIR / f"{track['uuid']}.%(ext)s"))
        # Upload to cache channel
        with open(filepath, 'rb') as f:
            sent = await bot.send_audio(CACHE_CHANNEL_ID, f, title=track['title'], performer=track['uploader'])
        # Store file_id
        await db.set_cache_info(track['uuid'], file_id=sent.audio.file_id, cache_msg_id=str(sent.id))
        # Also update track duration if available
        if info.get('duration'):
            track['duration'] = format_duration(info['duration'])
            await db.save_track(track)
        return sent.audio.file_id
    except Exception as e:
        logger.error(f"Download/cache failed: {e}")
        return None
    finally:
        if temp_file.exists():
            temp_file.unlink()

# ==========================  BOT HANDLERS  ==========================
@bot.on_startup
async def on_startup():
    global bot_username
    await db.init()
    me = await bot.get_me()
    bot_username = me.username
    logger.info(f"Bot started as @{bot_username}")
    # Cleanup stale download entries every 5 minutes
    asyncio.create_task(periodic_cleanup())

async def periodic_cleanup():
    while True:
        await asyncio.sleep(300)
        await db.cleanup_old_downloads()

@bot.on_message(command("start"))
async def start_cmd(message: Message):
    await db.add_user(message.chat.id)
    await message.reply(
        "🎶 *Welcome to SoundCloud Downloader Bot!*\n\n"
        "Send me a SoundCloud link (track or playlist) to download.\n"
        "Or just type any artist/song name to search.\n\n"
        "Use /settings to change audio quality.\n"
        "Use /help for more info."
    )

@bot.on_message(command("help"))
async def help_cmd(message: Message):
    help_text = (
        "📖 *Help & Commands*\n\n"
        "/start - Restart bot\n"
        "/help - Show this message\n"
        "/settings - Change audio quality (128/192/320 kbps)\n"
        "/me - Show your current settings\n\n"
        "*How to use:*\n"
        "• Send a SoundCloud track URL → receive track info + download button\n"
        "• Send a playlist/set URL → choose tracks to download\n"
        "• Send any text → search SoundCloud (10 results)\n"
        "• Click download button → bot sends MP3 (cached for future requests)\n\n"
        "For support contact: @YourSupportHandle"
    )
    await message.reply(help_text)

@bot.on_message(command("settings"))
async def settings_cmd(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        await message.reply("Please use this command in private chat.")
        return
    quality = await db.get_user_quality(message.chat.id)
    keyboard = get_settings_keyboard(quality)
    await message.reply(f"Current quality: *{quality} kbps*\nSelect desired bitrate:", reply_markup=keyboard)

@bot.on_message(command("me"))
async def me_cmd(message: Message):
    quality = await db.get_user_quality(message.chat.id)
    await message.reply(f"🎧 Your audio quality: *{quality} kbps*")

# Admin commands
@bot.on_message(command("stats"))
async def stats_cmd(message: Message):
    if message.author.id != ADMIN_USER_ID:
        return
    users = await db.get_all_users()
    await message.reply(f"📊 Total users: {len(users)}")

@bot.on_message(command("broadcast"))
async def broadcast_cmd(message: Message):
    if message.author.id != ADMIN_USER_ID:
        return
    text = message.text.replace('/broadcast', '', 1).strip()
    if not text:
        await message.reply("Usage: /broadcast message")
        return
    users = await db.get_all_users()
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.reply(f"Broadcast sent to {sent}/{len(users)} users.")

# Broadcast channel forwarder (if configured)
if BROADCAST_CHANNEL_ID:
    @bot.on_message(chat(BROADCAST_CHANNEL_ID))
    async def channel_broadcast_handler(message: Message):
        users = await db.get_all_users()
        for uid in users:
            try:
                await bot.forward_message(uid, message.chat.id, message.id)
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Broadcast to {uid} failed: {e}")

@bot.on_message(text & private)
async def handle_text_private(message: Message):
    await handle_any_text(message)

@bot.on_message(text & chat(ChatType.GROUP) | chat(ChatType.SUPERGROUP))
async def handle_text_group(message: Message):
    # Only reply if bot is mentioned or a SoundCloud link is present
    if not bot_username:
        return
    mention = f"@{bot_username}"
    if mention not in message.text and 'soundcloud.com' not in message.text:
        return
    await handle_any_text(message)

async def handle_any_text(message: Message):
    await db.add_user(message.chat.id)
    text = message.text.strip()
    # Remove bot mention
    if bot_username and f"@{bot_username}" in text:
        text = text.replace(f"@{bot_username}", "").strip()
    if not text:
        return

    urls = extract_urls(text)
    if urls:
        url = urls[0].split('?')[0]
        await process_url(message, url)
    else:
        await process_search(message, text)

async def process_url(message: Message, url: str):
    processing_msg = await message.reply("⏳ Processing your link...")
    # Check if playlist
    if is_playlist_url(url):
        await handle_playlist(message, url, processing_msg)
        return
    # Single track
    track = await db.get_track_by_url(url)
    if not track:
        try:
            info = await run_async(get_info_sync, url)
            track = {
                'uuid': f"sc_{info['id']}",
                'title': info.get('title', 'Unknown'),
                'uploader': info.get('uploader', 'Unknown'),
                'genre': info.get('genre', 'Unknown'),
                'upload_date': str(info.get('upload_date', ''))[:4],
                'webpage_url': info.get('webpage_url', url).split('?')[0],
                'thumbnail': info.get('thumbnail', ''),
                'duration': format_duration(info.get('duration', 0)),
                'file_id': None,
                'cache_msg_id': None,
            }
            await db.save_track(track)
        except Exception as e:
            await processing_msg.edit_text(f"❌ Failed to fetch track info: {e}")
            return
    else:
        # Ensure uuid starts with sc_ for compatibility
        if not track['uuid'].startswith('sc_'):
            track['uuid'] = f"sc_{track['uuid']}"

    await processing_msg.delete()
    caption = build_track_caption(track, bot_username)
    keyboard = get_track_keyboard(track['uuid'])
    if track.get('thumbnail'):
        await bot.send_photo(message.chat.id, track['thumbnail'], caption=caption, reply_markup=keyboard)
    else:
        await bot.send_message(message.chat.id, caption, reply_markup=keyboard)

async def handle_playlist(message: Message, url: str, progress_msg: Message):
    """Extract playlist tracks and let user choose."""
    try:
        info = await run_async(get_info_sync, url)
        entries = info.get('entries', [])
        if not entries:
            await progress_msg.edit_text("❌ No tracks found in this playlist.")
            return
        tracks = []
        for idx, e in enumerate(entries[:20]):  # limit to 20
            track_url = e.get('webpage_url') or f"{url}?track={e.get('id')}"
            tracks.append({
                'number': idx+1,
                'title': e.get('title', 'Unknown'),
                'uploader': e.get('uploader', 'Unknown'),
                'url': track_url,
                'uuid': f"sc_{e.get('id')}",
            })
        # Build inline keyboard with track selection
        buttons = []
        for t in tracks:
            buttons.append([InlineKeyboardButton(f"{t['number']}. {t['title'][:40]}", callback_data=f"playlist:{t['url']}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_playlist")])
        keyboard = InlineKeyboard(*[btn for row in buttons for btn in row], row_width=1)
        await progress_msg.edit_text(f"📀 *Playlist: {info.get('title', 'SoundCloud Set')}*\nChoose a track to download:", reply_markup=keyboard)
    except Exception as e:
        await progress_msg.edit_text(f"❌ Failed to load playlist: {e}")

async def process_search(message: Message, query: str):
    processing = await message.reply("🔍 Searching SoundCloud...")
    results = await search_soundcloud(query, max_results=15)
    await processing.delete()
    if not results:
        await message.reply("😔 No results found. Try a different query.")
        return
    # Store results temporarily in bot's memory (or use callback context)
    # For simplicity, we'll store in a global dict with message_id as key
    if not hasattr(bot, 'search_cache'):
        bot.search_cache = {}
    bot.search_cache[message.chat.id] = results
    keyboard = get_search_results_keyboard(results, page=0)
    await message.reply(f"🔎 *Results for:* {query}\nSelect a track:", reply_markup=keyboard)

# ==========================  CALLBACK QUERY HANDLERS  ==========================
@bot.on_callback_query()
async def on_callback(callback: CallbackQuery):
    data = callback.data
    user_id = callback.author.id
    chat_id = callback.message.chat.id

    # Settings actions
    if data.startswith("setqual:"):
        quality = data.split(":")[1]
        await db.set_user_quality(chat_id, quality)
        await callback.answer(f"Quality set to {quality} kbps")
        await callback.message.edit_text(f"✅ Audio quality updated to *{quality} kbps*")
        return

    # Search pagination
    if data.startswith("searchpage:"):
        page = int(data.split(":")[1])
        results = getattr(bot, 'search_cache', {}).get(chat_id, [])
        if not results:
            await callback.answer("Search results expired, please search again.")
            await callback.message.delete()
            return
        keyboard = get_search_results_keyboard(results, page)
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        await callback.answer()
        return

    # Select from search
    if data.startswith("select:"):
        url = data[7:]
        # Process as URL
        await callback.answer("Fetching track...")
        await callback.message.delete()
        # Simulate a new message with the URL
        await process_url(callback.message, url)
        return

    # Playlist selection
    if data.startswith("playlist:"):
        track_url = data[9:]
        await callback.answer("Processing track...")
        await callback.message.delete()
        await process_url(callback.message, track_url)
        return

    if data == "cancel_playlist":
        await callback.message.delete()
        await callback.answer("Cancelled")
        return

    # Audio download
    if data.startswith("audio:"):
        track_uuid = data[6:]
        track = await db.get_track_by_uuid(track_uuid)
        if not track:
            await callback.answer("❌ Track not found in database.")
            return

        # Prevent concurrent downloads for same user+track
        if await db.is_downloading(chat_id, track_uuid):
            await callback.answer("Download already in progress, please wait...", show_alert=True)
            return

        await db.add_downloading(chat_id, track_uuid)
        await callback.answer("⏳ Preparing download...")

        # Update message to show "processing"
        try:
            await callback.message.edit_reply_markup(reply_markup=InlineKeyboard(InlineKeyboardButton("⏳ Processing...", callback_data="ignore")))
        except:
            pass

        # Try to send cached audio
        sent = await send_cached_audio(chat_id, track)
        if not sent:
            # Need to download
            quality = await db.get_user_quality(chat_id)
            file_id = await download_and_cache(track, quality, chat_id)
            if file_id:
                await bot.send_audio(chat_id, file_id, title=track['title'], performer=track['uploader'])
            else:
                await bot.send_message(chat_id, "❌ Failed to download the track. Please try again later.")
        # Clean up download flag and delete original message
        await db.remove_downloading(chat_id, track_uuid)
        try:
            await callback.message.delete()
        except:
            pass
        return

    if data == "ignore":
        await callback.answer("Please wait, we're working on it...")

# ==========================  MAIN ==========================
if __name__ == "__main__":
    logger.info("Starting SoundCloud Bot...")
    bot.run()
