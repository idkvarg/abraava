import logging
import os
from uuid import uuid4
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from utils import is_valid_url, cb_make, cb_parse, convert_results_to_buttons
from crawler import Crawler
from downloader import download_audio, embed_id3_tags, edit_cover_exif
from i18n import translate

logger = logging.getLogger("abraava.handlers")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(translate("start", context=context))


async def handle_itunes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    query = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not query:
        await message.reply_text(translate("send_query", context=context))
        return

    results = await Crawler.Itunes.search(query)
    if not results:
        await message.reply_text(translate("no_results", context=context))
        return

    buttons = convert_results_to_buttons(results)
    await message.reply_text(translate("search.itunes", context=context), reply_markup=buttons)


async def handle_spotify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    query = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not query:
        await message.reply_text(translate("send_query", context=context))
        return

    results = await Crawler.Spotify.search(query)
    if not results:
        await message.reply_text(translate("no_results", context=context))
        return

    buttons = convert_results_to_buttons(results)
    await message.reply_text(translate("search.spotify", context=context), reply_markup=buttons)


async def handle_deezer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    query = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not query:
        await message.reply_text(translate("send_query", context=context))
        return

    results = await Crawler.Deezer.search(query)
    if not results:
        await message.reply_text(translate("no_results", context=context))
        return

    buttons = convert_results_to_buttons(results)
    await message.reply_text(translate("search.deezer", context=context), reply_markup=buttons)


async def handle_scloud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    query = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not query:
        await message.reply_text(translate("send_query", context=context))
        return

    results = await Crawler.SoundCloud.search(query)
    if not results:
        await message.reply_text(translate("no_results", context=context))
        return

    buttons = convert_results_to_buttons(results)
    await message.reply_text(translate("search.scloud", context=context), reply_markup=buttons)


async def handle_ytmusic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    query = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not query:
        await message.reply_text(translate("send_query", context=context))
        return

    results = await Crawler.YTMusic.search(query)
    if not results:
        await message.reply_text(translate("no_results", context=context))
        return

    buttons = convert_results_to_buttons(results)
    await message.reply_text(translate("search.ytmusic", context=context), reply_markup=buttons)


async def handle_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    await message.reply_text(translate("start", context=context))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message.text or "").strip()

    if len(text) == 0:
        await update.message.reply_text(translate("send_query", context=context), parse_mode="Markdown")
        return

    # URL case
    if is_valid_url(text):
        metadata = Crawler.extract_metadata(text)
        if not metadata:
            await update.message.reply_text(translate("error", context=context))
            return

        track_id = str(uuid4())
        buttons = [
            [InlineKeyboardButton("▶️ Preview", callback_data=cb_make("preview", track_id))],
            [InlineKeyboardButton("⬇️ Download", callback_data=cb_make("download", track_id))]
        ]

        await update.message.reply_text(
            f"🎶 {metadata.get('title', 'Unknown')} - {metadata.get('artistName', 'Unknown')}",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # Search case
    results = await Crawler.search(text)
    if not results:
        await update.message.reply_text(translate("no_results", context=context))
        return

    buttons = []
    for result in results:
        title = result.get("title") or result.get("trackName") or "Unknown"
        artist = " - " + (result.get("uploader") or result.get("artistName") or "")
        url = result.get("webpage_url") or ("ITUNES:" + str(result.get("trackId")))
        buttons.append([InlineKeyboardButton(f"{title}{artist}", callback_data=cb_make("info", url))])

    await update.message.reply_text("🔍 Results:", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action, payload = cb_parse(query.data)
    if action == "info":
        if not payload:
            await query.edit_message_text(translate("error", context=context))
            return

        links = await Crawler.get_links(payload)
        metadata = await Crawler.extract_metadata(links)
        download_link = Crawler.get_download_link(links)
        buttons = [
            [InlineKeyboardButton("⬇️ Download", callback_data=cb_make("download", download_link))]
        ]
        await context.bot.send_photo(
            chat_id=query.from_user.id,
            photo=metadata["coverUrl"],
            caption=f"""
🎧 Title: <code>{metadata["title"]}</code>
🎤 Artist: <code>{metadata["artist"]}</code>
💽 Album: <code>{metadata["album"]}</code>
🗓 Release Year: <code>{metadata["releaseDate"]}</code>
🌐 ISRC: QZK6L2154468

Track id:1D5Rgb6p3sg5E2OW8kBA9f
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        """        if metadata[0].get('previewUrl', False):
            buttons.append(
               [InlineKeyboardButton("▶️ Preview", callback_data=cb_make("preview", metadata['previewUrl']))])
"""

    elif action == "download":
        await query.edit_message_text(translate("downloading", context=context))
        await worker_download_and_send(context, query.message.chat_id, payload)

    elif action == "preview":
        await context.bot.send_audio(query.message.chat_id, audio=payload)

    else:
        await query.edit_message_text(translate("error", context=context))


async def worker_download_and_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str):
    status_msg = await context.bot.send_message(chat_id, "⏳ Downloading...")

    try:
        # Download the audio file
        mp3_path = await download_audio(url)

        # Fetch metadata
        metadata = await Crawler.extract_metadata(url)

        cover_bytes = None
        if metadata and metadata.get("artworkUrl100"):
            cover_url = metadata.get("artworkUrl100").replace("100x100", "600x600")

            async with httpx.AsyncClient() as client:
                response = await client.get(cover_url)
                if response.status_code == 200:
                    cover_bytes = edit_cover_exif(response.content, metadata)

        # Embed ID3 tags
        embed_id3_tags(mp3_path, metadata or {}, cover_bytes)

        # Send the audio file
        with open(mp3_path, "rb") as fh:
            filename = f"{metadata.get('artistName', 'Unknown')} - {metadata.get('title', 'Unknown')}.mp3"
            await context.bot.send_audio(
                chat_id,
                audio=InputFile(fh, filename=filename),
                caption="✅ Download completed!"
            )

        # Delete the "Downloading..." message
        await context.bot.delete_message(chat_id, status_msg.message_id)

    except Exception as e:
        logger.exception("Download/send failed")
        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ Failed")
    finally:
        if 'mp3_path' in locals() and os.path.exists(mp3_path):
            os.remove(mp3_path)


async def download_bytes(session, url: str) -> bytes:
    try:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()
    except Exception:
        logger.exception("Failed to fetch bytes from %s", url)
        return None


async def handle_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /setlang <language_code>")
        return

    lang_code = context.args[0].lower()
    supported_languages = ["en", "fa"]

    if lang_code not in supported_languages:
        await update.message.reply_text(f"Unsupported language. Supported languages: {', '.join(supported_languages)}")
        return

    context.user_data["lang"] = lang_code
    await update.message.reply_text(translate("start", lang_code))
