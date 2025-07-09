import os
import asyncio
import re
import time
from itertools import count
from math import floor
from urllib.parse import urlparse
import requests
from uuid import uuid4
from balethon import Client
from balethon.objects import Object, CallbackQuery
from watchfiles import awatch
from yt_dlp import YoutubeDL

bot = Client("1011430416:V6rCwbls3JUS38Zq9GZrGfMeRF2VDuPtVMaVxEWH")

platform_names = {
    "spotify.com": ["Spotify", "اسپاتیفای"],
    "music.apple.com": ["Apple Music", "اپل موزیک"],
    "itunes.apple.com": ["iTunes", "آیتونز"],
    "soundcloud.com": ["SoundCloud", "ساوندکلاود"],
    "deezer.com": ["Deezer", "دیزر"],
    "youtube.com": ["YouTube", "یوتیوب"],
    "youtu.be": ["YouTube", "یوتیوب"],
    "tidal.com": ["Tidal", "تایدل"],
    "amazon.com": ["Amazon Music", "آمازون موزیک"],
    "pandora.com": ["Pandora", "پاندورا"],
    "napster.com": ["Napster", "نپستر"],
    "bandcamp.com": ["Bandcamp", "بندکمپ"],
    "audiomack.com": ["Audiomack", "آدیومک"],
    "anghami.com": ["Anghami", "انغامی"],
    "boomplaymusic.com": ["Boomplay", "بوم‌پلی"],
}

cache = {}


def extract_link(text):
    text = text.strip()
    url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'
    match = re.search(url_pattern, text)
    return match.group(0) if match else None


def is_supported_songlink_platform(url):
    try:
        domain = urlparse(url).netloc.lower()
        for platform_name in platform_names.keys():
            if domain == platform_name or domain.endswith("." + platform_name):
                print(f'Song.Link-supported link detected: "{platform_names[platform_name][0]}"!')
                return True
        return False
    except Exception:
        return False


"""
def search_soundcloud(query):
    with YoutubeDL({"quiet": True}) as ydl:
        try:
            return ydl.extract_info(f"scsearch5:{query}", download=False)["entries"]
        except:
            return []
"""


def classify_results(results):
    classified_results = []
    for result in results:
        classified_result = {
            "id": "song:itunes:" + str(result.get("trackId") or result.get("albumName")),
            "name": result.get("trackName") or result.get("title") or "بی‌نام",
            "artist": result.get("artistName") or result.get("artistName"),
            "album": result.get("collectionName") or result.get("albumName"),
            "cover": result.get("artworkUrl100").replace("100x100", "500x500"),
            "url": result.get("trackViewUrl") or result.get("url"),
        }

        classified_results.append(classified_result)
    return classified_results


def search_itunes(query, limit):
    try:
        print("Fetching Data From iTunes...")
        results = requests.get("https://itunes.apple.com/search", params={
            "term": query,
            "media": "music",
            "limit": limit
        }).json().get("results", [])
        return classify_results(results)
    except:
        return []


def search_scloud(query, limit):
    print("Fetching Data From Soundcloud...")
    try:
        return []
    except:
        return []


def search(chat_id, query, limit=10):
    itunes_results = search_itunes(query, floor(limit / 2))
    scloud_results = search_scloud(query, limit - len(itunes_results))
    results = itunes_results + scloud_results
    if len(results) > limit:
        results = results[:limit]
    elif len(results) < limit:
        itunes_results = search_itunes(query, limit - len(results))
        results += itunes_results
    classified_results = {}
    for result in results:
        classified_results[result["id"]] = result
    cache[chat_id] = classified_results
    return classified_results


def fetch_songlink(url):
    try:
        r = requests.get("https://api.song.link/v1-alpha.1/links", params={"url": url})
        return r.json() if r.status_code == 200 else None
    except:
        return None


def extract_itunes(data):
    platforms = data.get("linksByPlatform", {})
    itunes = platforms.get("itunes", {})
    eid = itunes.get("entityUniqueId")
    return data.get("entitiesByUniqueId", {}).get(eid)


def extract_songlink(data):
    platforms = data.get("linksByPlatform", {})
    itunes = platforms.get("itunes", {})
    eid = itunes.get("entityUniqueId")
    return data.get("entitiesByUniqueId", {}).get(eid, {})


def fetch_songlink_priority_url(data):
    platforms = data.get("linksByPlatform", {})
    return (
            platforms.get("soundcloud", {}).get("url") or
            platforms.get("youtube", {}).get("url")
    )


def format_meta(meta):
    return (
        f"\U0001F3B5 *{meta.get('name')}*\n"
        f"\U0001F464 {meta.get('artist')}\n"
        f"🖼 {meta.get('album')}\n"
    )


def delete_file(path):
    if os.path.exists(path):
        os.remove(path)


async def download_and_send(chat_id, url):
    filename = f"{uuid4()}.mp3"
    msg = await bot.send_message(chat_id, "⏳ در حال دانلود فایل...")

    last_update_time = 0

    def progress_hook(d):
        nonlocal last_update_time
        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time >= 1:
                percent = d.get('_percent_str', '').strip()
                speed = d.get('_speed_str', '').strip()
                eta = d.get('_eta_str', '').strip()
                text = f"⬇️ در حال دانلود...\nدرصد: {percent}\nسرعت: {speed}\nزمان: {eta}"
                asyncio.create_task(bot.edit_message_text(chat_id, msg.id, text))
                last_update_time = now

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

    try:
        with open(filename, 'rb') as f:
            await bot.send_audio(chat_id, audio=f)
    except Exception as e:
        await bot.send_message(chat_id, f"⛔ خطا در ارسال فایل: {e}")
    finally:
        delete_file(filename)


async def send_song_info(chat_id, meta, song_data):
    data = cache[chat_id][meta]
    tid = str(uuid4())
    keyboard = []
    """
    if preview:
        keyboard.append([{"text": "🎧 پخش پیش‌نمایش", "callback_data": f"preview_{preview}"}])

    keyboard.append([{"text": "⬇️ دریافت فایل", "callback_data": f"download_{tid}"}])
    """
    print(data["cover"])
    await bot.send_photo(
        chat_id=chat_id,
        photo=data["cover"],
        caption=format_meta(data),
    )
    """reply_markup={"inline_keyboard": keyboard}"""


@bot.on_message()
async def handle_message(message):
    chat_id = message.chat.id
    text = message.text.strip()
    if not text:
        return await bot.send_message(chat_id, "لطفاً نام آهنگ را بفرست.")
    link = extract_link(text)
    if link:
        print("Link Detected...")
        if is_supported_songlink_platform(link):
            await handle_callback(
                CallbackQuery(
                    data="open:"
                )
            )
    else:
        results = search(chat_id, text)
        if not results:
            return await bot.send_message(chat_id, "⚠️ هیچ نتیجه‌ای یافت نشد.")

        keyboard = []
        text = "نتایج جستجو:\n"
        for result in results.values():
            keyboard.append(
                [
                    {
                        "text": f"{result["name"]} - {result['artist']}",
                        "callback_data": f"open:{result['id']}"
                    }
                ]
            )
        await bot.send_message(
            chat_id,
            text,
            reply_markup={"inline_keyboard": keyboard}
        )


@bot.on_callback_query()
async def handle_callback(callback_query):
    chat_id = callback_query.message.chat.id
    data = callback_query.data

    if data.startswith("preview_"):
        url = data[8:]
        return await bot.send_voice(chat_id, voice=url)

    elif data.startswith("download_"):
        tid = data[9:]
        song_data = download_links.get(tid)
        if not song_data:
            return await bot.send_message(chat_id, "❌ لینک دانلود موجود نیست.")

        url = fetch_songlink_priority_url(song_data)
        if url:
            await callback_query.answer("⬇️ در حال دانلود...")
            return await download_and_send(chat_id, url)
        else:
            return await bot.send_message(chat_id, "❌ فایل قابل دانلود نیست.")

    elif data.startswith("open:"):
        await callback_query.answer("⏳ دریافت اطلاعات...")
        result_id = data[5:]
        song_data = fetch_songlink(cache[chat_id][result_id]['url'])
        if not song_data:
            return await bot.send_message(chat_id, "⛔ خطا در ارتباط با Song.link")
        meta = extract_itunes(song_data)
        if meta:
            return await send_song_info(chat_id, result_id, song_data)

        fallback_url = fetch_songlink_priority_url(song_data)
        if fallback_url:
            return await download_and_send(chat_id, fallback_url)
        else:
            return await bot.send_message(chat_id, "❌ هیچ لینکی برای دانلود یافت نشد.")


bot.run()
