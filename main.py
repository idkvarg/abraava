import os
import tempfile
import asyncio
import logging
from uuid import uuid4
from io import BytesIO
import urllib.parse

import aiohttp
from yt_dlp import YoutubeDL
from cachetools import TTLCache

from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
import piexif
from PIL import Image

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------- Logging ----------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("music_bot")

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN environment variable not set")
    raise RuntimeError("BOT_TOKEN environment variable not set")

# ---------- Caches ----------
search_cache = TTLCache(maxsize=1000, ttl=3600)
download_links_cache = TTLCache(maxsize=1000, ttl=3600)

YTDL_EXTRACT_OPTS = {"quiet": True, "extract_flat": True, "skip_download": True}

# ---------- Helpers ----------
def short_cb(prefix: str, payload: str) -> str:
    """
    Create a short callback string by URL-safe encoding the payload.
    Keeps overall length small to avoid Telegram limits.
    """
    return f"{prefix}:{urllib.parse.quote_plus(payload)}"

def parse_cb(data: str):
    """
    Parse callback data of form prefix:payload (payload URL-unquoted).
    Returns (prefix, payload)
    """
    if ":" not in data:
        return data, ""
    prefix, payload = data.split(":", 1)
    return prefix, urllib.parse.unquote_plus(payload)

def format_song_info(metadata: dict) -> str:
    title = metadata.get("trackName") or metadata.get("title") or "Unknown Title"
    artist = metadata.get("artistName") or metadata.get("uploader") or "Unknown Artist"
    album = metadata.get("collectionName") or metadata.get("album") or "Unknown Album"
    release = (metadata.get("releaseDate") or "")[:10]
    genre = metadata.get("primaryGenreName") or metadata.get("genre") or "Unknown"
    return (
        f"🎵 *{title}*\n"
        f"👤 *Artist:* {artist}\n"
        f"💿 *Album:* {album}\n"
        f"📅 *Released:* {release}\n"
        f"🎶 *Genre:* {genre}"
    )

async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict = None, timeout: int = 15):
    try:
        async with session.get(url, params=params, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        logger.warning("HTTP fetch_json error for %s: %s", url, e)
    return None

# ---------- Search ----------
async def search_soundcloud(query: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_soundcloud_sync, query)

def _search_soundcloud_sync(query: str):
    try:
        logger.debug("Running yt-dlp soundcloud search for query=%s", query)
        with YoutubeDL(YTDL_EXTRACT_OPTS) as ydl:
            res = ydl.extract_info(f"scsearch5:{query}", download=False)
            return res.get("entries", [])[:5]
    except Exception as e:
        logger.warning("SoundCloud search error: %s", e)
        return []

async def search_itunes(query: str):
    async with aiohttp.ClientSession() as session:
        res = await fetch_json(session, "https://itunes.apple.com/search", params={"term": query, "media": "music", "limit": 5})
        return res.get("results", []) if res else []

async def fetch_songlink(url: str):
    async with aiohttp.ClientSession() as session:
        return await fetch_json(session, "https://api.song.link/v1-alpha.1/links", params={"url": url})

def extract_itunes_data(songlink_data: dict) -> dict:
    platforms = songlink_data.get("linksByPlatform", {}) or {}
    itunes = platforms.get("itunes", {}) or {}
    entity_id = itunes.get("entityUniqueId")
    return (songlink_data.get("entitiesByUniqueId", {}) or {}).get(entity_id, {}) or {}

def get_priority_download_url(songlink_data: dict) -> str | None:
    platforms = songlink_data.get("linksByPlatform", {}) or {}
    return (
        (platforms.get("soundcloud") or {}).get("url")
        or (platforms.get("youtube") or {}).get("url")
        or (platforms.get("youtubeMusic") or {}).get("url")
    )

# ---------- Download & metadata ----------
async def download_media_to_temp(url: str, ydl_opts: dict) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_media_sync, url, ydl_opts)

def _download_media_sync(url: str, ydl_opts: dict) -> str:
    tmpdir = tempfile.gettempdir()
    filename = os.path.join(tmpdir, f"{uuid4()}.%(ext)s")
    ydl_opts_copy = dict(ydl_opts)
    ydl_opts_copy["outtmpl"] = filename
    logger.info("Starting yt-dlp download for url=%s", url)
    with YoutubeDL(ydl_opts_copy) as ydl:
        ydl.download([url])
    base = filename.split(".%(ext)s")[0]
    for ext in ("mp3", "m4a", "webm", "opus", "wav", "aac", "flac"):
        p = f"{base}.{ext}"
        if os.path.exists(p):
            logger.info("Downloaded file found: %s", p)
            return p
    for f in os.listdir(tmpdir):
        if f.startswith(os.path.basename(base)):
            p = os.path.join(tmpdir, f)
            logger.info("Downloaded file found by prefix: %s", p)
            return p
    logger.error("Downloaded file not found for base=%s", base)
    raise FileNotFoundError("Downloaded file not found")

def embed_id3_tags(mp3_path: str, metadata: dict, cover_bytes: bytes | None = None):
    logger.info("Embedding ID3 tags into %s", mp3_path)
    try:
        try:
            tags = EasyID3(mp3_path)
        except ID3NoHeaderError:
            tags = EasyID3()
            tags.save(mp3_path)
        tags = EasyID3(mp3_path)
        title = metadata.get("trackName") or metadata.get("title")
        artist = metadata.get("artistName") or metadata.get("artist") or metadata.get("uploader")
        album = metadata.get("collectionName") or metadata.get("album")
        date = (metadata.get("releaseDate") or "")[:10]
        genre = metadata.get("primaryGenreName") or metadata.get("genre")
        if title: tags["title"] = title
        if artist: tags["artist"] = artist
        if album: tags["album"] = album
        if date: tags["date"] = date
        if genre: tags["genre"] = genre
        tags.save(mp3_path)
        if cover_bytes:
            audio = ID3(mp3_path)
            audio.delall("APIC")
            audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_bytes))
            audio.save(mp3_path)
        logger.debug("ID3 tagging complete for %s", mp3_path)
    except Exception as e:
        logger.exception("Failed to embed ID3 tags: %s", e)

def edit_image_exif(image_bytes: bytes, metadata: dict) -> bytes:
    logger.info("Editing image EXIF")
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        out_io = BytesIO()
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        artist = metadata.get("artistName") or metadata.get("artist") or ""
        copyright_text = metadata.get("copyright") or ""
        description = metadata.get("trackName") or metadata.get("title") or ""
        if artist:
            exif_dict["0th"][piexif.ImageIFD.Artist] = artist
        if copyright_text:
            exif_dict["0th"][piexif.ImageIFD.Copyright] = copyright_text
        if description:
            exif_dict["0th"][piexif.ImageIFD.ImageDescription] = description
        exif_bytes = piexif.dump(exif_dict)
        img.save(out_io, format="JPEG", exif=exif_bytes, quality=95)
        logger.debug("EXIF edit complete")
        return out_io.getvalue()
    except Exception as e:
        logger.exception("Failed to edit image EXIF: %s", e)
        return image_bytes

async def fetch_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, timeout=20) as resp:
            resp.raise_for_status()
            return await resp.read()
    except Exception as e:
        logger.warning("Failed to fetch bytes from %s: %s", url, e)
    return None

# ---------- Send details & download ----------
async def send_song_details(context: ContextTypes.DEFAULT_TYPE, chat_id: int, metadata: dict, songlink_data: dict, reply_to_message_id: int | None = None):
    caption = format_song_info(metadata)
    artwork_url = (metadata.get("artworkUrl100") or "").replace("100x100", "600x600")
    download_id = str(uuid4())
    download_links_cache[download_id] = songlink_data
    logger.info("Prepared song details; download_id=%s", download_id)

    preview_url = metadata.get("previewUrl")
    download_url = get_priority_download_url(songlink_data)
    buttons = []
    if preview_url:
        buttons.append(InlineKeyboardButton("🎧 Preview", callback_data=short_cb("preview", preview_url)))
    if download_url:
        buttons.append(InlineKeyboardButton("⬇️ Download", callback_data=short_cb("download", download_id)))
    buttons.append(InlineKeyboardButton("🔍 Search Again", callback_data="search_again"))
    keyboard = InlineKeyboardMarkup([buttons])

    photo_bytes = None
    if artwork_url:
        async with aiohttp.ClientSession() as session:
            photo_bytes = await fetch_bytes(session, artwork_url)
            if photo_bytes:
                photo_bytes = edit_image_exif(photo_bytes, metadata)

    try:
        if photo_bytes:
            bio = BytesIO(photo_bytes); bio.name = "cover.jpg"
            await context.bot.send_photo(chat_id=chat_id, photo=InputFile(bio), caption=caption, parse_mode="Markdown", reply_markup=keyboard, reply_to_message_id=reply_to_message_id)
            logger.info("Sent song details with photo to chat_id=%s", chat_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="Markdown", reply_markup=keyboard, reply_to_message_id=reply_to_message_id)
            logger.info("Sent song details (no photo) to chat_id=%s", chat_id)
    except Exception as e:
        logger.exception("Failed sending song details: %s", e)
        await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="Markdown", reply_markup=keyboard, reply_to_message_id=reply_to_message_id)

async def download_and_send_audio(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str, metadata: dict, reply_to_message_id: int | None = None):
    logger.info("Initiating download_and_send_audio for chat_id=%s url=%s", chat_id, url)
    status = await context.bot.send_message(chat_id=chat_id, text="⏳ Downloading file...")
    ydl_opts = {"format": "bestaudio/best", "quiet": True, "noplaylist": True, "postprocessors": [{ "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192" }]}
    mp3_path = None
    try:
        mp3_path = await download_media_to_temp(url, ydl_opts)
        cover_bytes = None
        artwork_url = (metadata.get("artworkUrl100") or "").replace("100x100", "600x600")
        if artwork_url:
            async with aiohttp.ClientSession() as session:
                cover_bytes = await fetch_bytes(session, artwork_url)
        if mp3_path.lower().endswith(".mp3"):
            embed_id3_tags(mp3_path, metadata, cover_bytes)
        else:
            try:
                embed_id3_tags(mp3_path, metadata, cover_bytes)
            except Exception:
                logger.debug("Skipping ID3 embed for non-mp3 file %s", mp3_path)
        with open(mp3_path, "rb") as f:
            await context.bot.send_audio(chat_id=chat_id, audio=InputFile(f, filename=os.path.basename(mp3_path)), caption="✅ Download completed!", reply_to_message_id=reply_to_message_id)
        await context.bot.delete_message(chat_id=chat_id, message_id=status.message_id)
        logger.info("Sent audio to chat_id=%s and deleted status message", chat_id)
    except Exception as e:
        logger.exception("Download/send failed: %s", e)
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=status.message_id, text=f"❌ Download error: {e}")
        except Exception:
            logger.exception("Failed to update status message with error")
    finally:
        if mp3_path and os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
                logger.debug("Cleaned up temp file %s", mp3_path)
            except Exception:
                logger.exception("Failed to remove temp file %s", mp3_path)

# ---------- Handlers ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("User %s (%s) invoked /start", user.first_name if user else "unknown", user.id if user else "unknown")
    await update.message.reply_text("Send a song name to search.")

async def incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id
    logger.info("Incoming message from user_id=%s text=%s", user.id if user else None, text)
    if not text:
        await update.message.reply_text("🎵 Please send me a song name to search!")
        return
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    soundcloud_results, itunes_results = await asyncio.gather(search_soundcloud(text), search_itunes(text))
    all_results = (soundcloud_results or []) + (itunes_results or [])
    if not all_results:
        await update.message.reply_text("❌ No results found. Try a different search term.")
        logger.info("No results for query=%s", text)
        return
    search_id = str(uuid4())
    search_cache[search_id] = {"results": all_results[:8], "timestamp": asyncio.get_event_loop().time(), "query": text}
    logger.info("Cached search_id=%s for query=%s with %d results", search_id, text, len(all_results))
    buttons = []
    for idx, item in enumerate(all_results[:8], start=1):
        title = item.get("title") or item.get("trackName") or "Unknown Title"
        artist = item.get("uploader") or item.get("artistName") or "Unknown Artist"
        label = f"{idx}. {title[:30]} - {artist[:20]}"
        payload = f"{search_id}|{idx-1}"
        buttons.append([InlineKeyboardButton(label, callback_data=short_cb("select", payload))])
    buttons.append([InlineKeyboardButton("🔍 New Search", callback_data="new_search")])
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(f"🔍 Found {len(all_results)} results for: *{text}*", parse_mode="Markdown", reply_markup=keyboard)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prefix, payload = parse_cb(query.data)
    user = query.from_user
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    logger.info("Callback received from user_id=%s prefix=%s payload=%s", user.id if user else None, prefix, payload)
    if prefix in ("new_search", "search_again"):
        await context.bot.send_message(chat_id=chat_id, text="🔍 Send me the name of the song you want to search:")
        return
    if prefix == "preview":
        preview_url = payload
        logger.info("Sending preview URL=%s to chat_id=%s", preview_url, chat_id)
        try:
            await context.bot.send_audio(chat_id=chat_id, audio=preview_url, reply_to_message_id=message_id)
        except Exception:
            logger.exception("Failed to send preview")
            await context.bot.send_message(chat_id=chat_id, text="Unable to play preview.")
        return
    if prefix == "download":
        download_id = payload
        song_data = download_links_cache.get(download_id)
        if not song_data:
            logger.warning("Download link expired for download_id=%s", download_id)
            await context.bot.send_message(chat_id=chat_id, text="❌ Download link expired. Please search again.")
            return
        download_url = get_priority_download_url(song_data)
        if download_url:
            itunes_meta = extract_itunes_data(song_data) or {}
            logger.info("Starting background download for chat_id=%s download_url=%s", chat_id, download_url)
            context.application.create_task(download_and_send_audio(context, chat_id, download_url, itunes_meta, reply_to_message_id=message_id))
        else:
            logger.warning("No download available in songlink_data for download_id=%s", download_id)
            await context.bot.send_message(chat_id=chat_id, text="❌ No download available for this track.")
        return
    if prefix == "select":
        # payload is "search_id|index"
        if "|" not in payload:
            logger.warning("Malformed select payload: %s", payload)
            return
        search_id, idx_str = payload.split("|", 1)
        try:
            result_index = int(idx_str)
        except ValueError:
            logger.warning("Invalid result index in payload: %s", payload)
            return
        search_data = search_cache.get(search_id)
        if not search_data:
            logger.warning("Search results expired for search_id=%s", search_id)
            await context.bot.send_message(chat_id=chat_id, text="❌ Search results expired. Please search again.")
            return
        results = search_data["results"]
        if result_index >= len(results):
            logger.warning("Invalid selection index %d for search_id=%s", result_index, search_id)
            await context.bot.send_message(chat_id=chat_id, text="❌ Invalid selection.")
            return
        selected_item = results[result_index]
        item_url = selected_item.get("webpage_url") or selected_item.get("trackViewUrl")
        if not item_url:
            logger.warning("No URL available for selected item index=%d search_id=%s", result_index, search_id)
            await context.bot.send_message(chat_id=chat_id, text="❌ No URL available for this track.")
            return
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        songlink_data = await fetch_songlink(item_url)
        if not songlink_data:
            logger.warning("Could not fetch song.link data for url=%s", item_url)
            await context.bot.send_message(chat_id=chat_id, text="❌ Could not fetch track information.")
            return
        itunes_meta = extract_itunes_data(songlink_data)
        if itunes_meta:
            logger.info("Sending song details for selection index=%d search_id=%s", result_index, search_id)
            await send_song_details(context, chat_id, itunes_meta, songlink_data, reply_to_message_id=message_id)
        else:
            download_url = get_priority_download_url(songlink_data)
            if download_url:
                logger.info("No itunes metadata; starting direct download for url=%s", download_url)
                context.application.create_task(download_and_send_audio(context, chat_id, download_url, {}, reply_to_message_id=message_id))
            else:
                logger.warning("No download available for selected item")
                await context.bot.send_message(chat_id=chat_id, text="❌ No download available for this track.")

# ---------- Main ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_message))
    app.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Bot starting with polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
