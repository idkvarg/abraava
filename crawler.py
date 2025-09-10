import asyncio
import logging
from typing import List, Dict, Any, Optional

import httpx
from yt_dlp import YoutubeDL
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import deezer
from config import SPOTIPY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

from config import YTDL_EXTRACT_OPTS

logger = logging.getLogger("abraava.Crawler")


class Crawler:
    ################
    # Prettify Results #
    ################
    @staticmethod
    def _prettify_results(raw_results: Any, platform: str) -> List[Dict[str, Any]]:
        results = []

        if platform == "itunes":
            data = raw_results.json().get("results", [])
            for r in data:
                if r.get("wrapperType") == "track":
                    results.append({
                        "title": r.get("trackName"),
                        "url": r.get("trackViewUrl"),
                        "artist": r.get("artistName"),
                        "album": r.get("collectionName"),
                        "coverUrl": r.get("artworkUrl100"),
                    })

        elif platform == "spotify":
            items = raw_results.get("tracks", {}).get("items", [])
            for t in items:
                results.append({
                    "title": t["name"],
                    "url": t["external_urls"]["spotify"],
                    "artist": ", ".join([a["name"] for a in t["artists"]]),
                    "album": t["album"]["name"],
                    "coverUrl": t["album"]["images"][0]["url"] if t["album"]["images"] else None
                })

        elif platform == "deezer":
            for t in raw_results:
                results.append({
                    "title": t.title,
                    "url": t.link,
                    "artist": t.artist.name,
                    "album": t.album.title,
                    "coverUrl": t.album.cover_medium,
                })

        elif platform in ["soundcloud", "ytmusic"]:
            for t in raw_results:
                results.append({
                    "title": t.get("title"),
                    "url": t.get("webpage_url"),
                    "artist": t.get("uploader"),
                    "album": t.get("album") or "",
                    "coverUrl": t.get("thumbnail") or "",
                })

        return results

    ################
    # SoundCloud   #
    ################
    class SoundCloud:
        @staticmethod
        def _search_sync(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            try:
                with YoutubeDL(YTDL_EXTRACT_OPTS) as ydl:
                    res = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                    return res.get("entries", []) if res else []
            except Exception as e:
                logger.exception("SoundCloud search failed: %s", e)
                return []

        @staticmethod
        async def search(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            loop = asyncio.get_running_loop()
            raw_results = await loop.run_in_executor(None, Crawler.SoundCloud._search_sync, query, limit)
            return Crawler._prettify_results(raw_results, "soundcloud")

    ################
    # YouTube Music #
    ################
    class YTMusic:
        @staticmethod
        def _search_sync(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            try:
                with YoutubeDL(YTDL_EXTRACT_OPTS) as ydl:
                    res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                    return res.get("entries", []) if res else []
            except Exception as e:
                logger.exception("YouTube Music search failed: %s", e)
                return []

        @staticmethod
        async def search(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            loop = asyncio.get_running_loop()
            raw_results = await loop.run_in_executor(None, Crawler.YTMusic._search_sync, query, limit)
            return Crawler._prettify_results(raw_results, "ytmusic")

    ################
    # iTunes       #
    ################
    class Itunes:
        @staticmethod
        async def search(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
            offset = (page - 1) * limit
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    res = await client.get("https://itunes.apple.com/search", params={
                        "term": query, "media": "music", "limit": limit, "offset": offset
                    })
                    res.raise_for_status()
                    return Crawler._prettify_results(res, "itunes")
            except Exception as e:
                logger.error("iTunes search failed: %s", e)
                return []

    ################
    # Spotify      #
    ################
    class Spotify:
        client = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))

        @staticmethod
        async def search(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            loop = asyncio.get_running_loop()

            def _sync_search():
                return Crawler.Spotify.client.search(q=query, type="track", limit=limit)

            raw_results = await loop.run_in_executor(None, _sync_search)
            return Crawler._prettify_results(raw_results, "spotify")

    ################
    # Deezer       #
    ################
    class Deezer:
        client = deezer.Client()

        @staticmethod
        async def search(query: str, limit: int = 7):
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, Crawler.Deezer._sync_search, query, limit)

        @staticmethod
        def _sync_search(query: str, limit: int):
            try:
                results = Crawler.Deezer.client.search(query)  # Returns Track objects
                tracks = []
                for track in results[:limit]:
                    tracks.append({
                        "title": track.title,
                        "artist": track.artist.name,
                        "album": track.album.title if track.album else "",
                        "coverUrl": track.album.cover if track.album else None,
                        "url": track.link
                    })
                return tracks
            except Exception as e:
                logging.exception("Deezer search failed: %s", e)
                return []

    ################
    # Unified Search #
    ################
    @staticmethod
    async def search_all(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
        tasks = [
            Crawler.SoundCloud.search(query, limit),
            Crawler.YTMusic.search(query, limit),
            Crawler.Itunes.search(query, limit, page),
            Crawler.Spotify.search(query, limit),
            Crawler.Deezer.search(query, limit)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                logger.error("Search error: %s", res)
                continue
            final_results.extend(res)
        return final_results
