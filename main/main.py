import os
import sqlite3
import yt_dlp
from balethon import Client
from balethon.conditions import private
from balethon.objects import Message

# Read from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ARCHIVE_CHANNEL_ID = int(os.getenv("ARCHIVE_CHANNEL_ID"))

app = Client(BOT_TOKEN)

# Initialize SQLite Database
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
    await message.reply("Searching and processing...")

    # yt-dlp options for extracting audio and getting info
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
            # Extract info without downloading first to get the ID
            info = ydl.extract_info(query, download=False)

            # If it's a search, get the first result
            if 'entries' in info:
                info = info['entries'][0]

            yt_id = info['id']
            title = info['title']

            # Check if track is in our database
            archived_msg_id = get_track_from_db(yt_id)

            if archived_msg_id:
                # Track exists, forward it from the archive channel
                await message.reply("Found in archive! Sending...")
                # In Bale/Telegram, we copy or forward the message
                await app.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=ARCHIVE_CHANNEL_ID,
                    message_id=archived_msg_id
                )
            else:
                # Track does not exist, download it
                await message.reply("Downloading from YouTube...")
                ydl.download([info['webpage_url']])

                filename = f"{yt_id}.mp3"

                # Send to archive channel
                archive_msg = await app.send_document(
                    chat_id=ARCHIVE_CHANNEL_ID,
                    document=filename,
                    caption=f"Title: {title}\nID: {yt_id}"
                )

                # Save to database
                save_track_to_db(yt_id, title, archive_msg.id)

                # Send to user
                await app.send_document(
                    chat_id=message.chat.id,
                    document=filename,
                    caption=title
                )

                # Clean up local file
                if os.path.exists(filename):
                    os.remove(filename)

    except Exception as e:
        await message.reply(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    app.run()
