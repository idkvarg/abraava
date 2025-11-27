import asyncio
import logging
import re
import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from yt_dlp import YoutubeDL
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import deezer

from config import (
    SPOTIPY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    YTDL_EXTRACT_OPTS,
    DOWNLOAD_LINKS_CACHE,
)

logger = logging.getLogger("abraava.crawler")


class Crawler:
    """
    Unified crawler / search helper for multiple music platforms.
    Contains nested platform helpers (SoundCloud, YTMusic, Itunes, Spotify, Deezer)
    and utilities to extract metadata and resolve download links (via Song.link).
    """

    @staticmethod
    def _prettify_results(raw_results: Any, platform: str) -> List[Dict[str, Any]]:
        """
        Convert raw results from different sources into a common concise dict format:
        { title, url, artist, album, coverUrl }
        """
        results: List[Dict[str, Any]] = []

        if platform == "spotify":
            # spotipy search returns dict with 'tracks' -> 'items'
            items = raw_results.get("tracks", {}).get("items", []) if isinstance(raw_results, dict) else []
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
                    "album": t.album.title if t.album else "",
                    "coverUrl": t.album.cover_medium if t.album else None,
                })

        elif platform in ["soundcloud", "ytmusic"]:
            # results from yt-dlp extract_info (search) return list of dicts
            for t in raw_results:
                results.append({
                    "title": t.get("title"),
                    "url": t.get("webpage_url"),
                    "artist": t.get("uploader"),
                    "album": t.get("album") or "",
                    "coverUrl": t.get("thumbnail") or "",
                })

        elif platform == "itunes":
            # pass-through the httpx.Response or dict returned by iTunes API
            items = []
            if isinstance(raw_results, dict):
                items = raw_results.get("results", [])
            elif hasattr(raw_results, "json"):
                items = raw_results.json().get("results", [])
            for t in items:
                results.append({
                    "title": t.get("trackName") or t.get("collectionName"),
                    "url": t.get("trackViewUrl") or t.get("collectionViewUrl"),
                    "artist": t.get("artistName"),
                    "album": t.get("collectionName") or "",
                    "coverUrl": (t.get("artworkUrl100") or "").replace("100x100", "400x400") if t.get("artworkUrl100") else None,
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
        async def search(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
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
        async def search(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
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
                    return Crawler._prettify_results(res.json(), "itunes")
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
        async def search(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
            loop = asyncio.get_running_loop()
            offset = (page - 1) * limit

            def _sync_search():
                return Crawler.Spotify.client.search(q=query, type="track", limit=limit, offset=offset)

            raw_results = await loop.run_in_executor(None, _sync_search)
            return Crawler._prettify_results(raw_results, "spotify")

    ################
    # Deezer       #
    ################
    class Deezer:
        client = deezer.Client()

        @staticmethod
        def _sync_search(query: str, limit: int = 7) -> List[Dict[str, Any]]:
            try:
                results = Crawler.Deezer.client.search(query)
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
                logger.exception("Deezer search failed: %s", e)
                return []

        @staticmethod
        async def search(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, Crawler.Deezer._sync_search, query, limit)

    ################
    # Unified Search #
    ################
    @staticmethod
    async def search_all(query: str, limit: int = 7, page: int = 1) -> List[Dict[str, Any]]:
        tasks = [
            Crawler.SoundCloud.search(query, limit, page),
            Crawler.YTMusic.search(query, limit, page),
            Crawler.Itunes.search(query, limit, page),
            Crawler.Spotify.search(query, limit, page),
            Crawler.Deezer.search(query, limit, page)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                logger.error("Search error: %s", res)
                continue
            final_results.extend(res)
        return final_results

    ################
    # Metadata / Links (URL Handling) #
    ################
    @staticmethod
    async def extract_metadata(url: str) -> Optional[Dict[str, Any]]:
        """
        Extract metadata for a given track URL. Handles Spotify, Deezer, iTunes, and falls back to yt-dlp.
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname.lower() if parsed.hostname else ""

            # --------- Spotify ---------
            if "spotify.com" in hostname:
                track_id = url.split("/")[-1].split("?")[0]
                loop = asyncio.get_running_loop()

                def _get_spotify():
                    sp = Crawler.Spotify.client
                    track = sp.track(track_id)
                    return {
                        "url": url,
                        "title": track["name"],
                        "artist": ", ".join([a["name"] for a in track["artists"]]),
                        "album": track["album"]["name"],
                        "coverUrl": track["album"]["images"][0]["url"] if track["album"]["images"] else None,
                        "releaseDate": track["album"].get("release_date"),
                        "isrc": track.get("external_ids", {}).get("isrc", "")
                    }

                return await loop.run_in_executor(None, _get_spotify)

            # --------- Deezer ---------
            elif "deezer.com" in hostname:
                dz = Crawler.Deezer.client
                match = re.search(r"/track/(\d+)", url)
                if match:
                    track_id = int(match.group(1))
                    track = dz.get_track(track_id)
                    return {
                        "url": url,
                        "title": track.title,
                        "artist": track.artist.name,
                        "album": track.album.title if track.album else "",
                        "coverUrl": track.album.cover if track.album else None,
                        "releaseDate": getattr(track, "release_date", None),
                        "isrc": getattr(track, "isrc", None)
                    }

            # --------- iTunes / Apple Music ---------
            elif "itunes.apple.com" in hostname or "music.apple.com" in hostname:
                path_parts = parsed.path.strip("/").split("/")
                track_id = path_parts[-1] if path_parts[-1].isdigit() else None

                if track_id:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        res = await client.get("https://itunes.apple.com/lookup", params={"id": track_id})
                        res.raise_for_status()
                        results = res.json().get("results", [])
                        if results:
                            r = results[0]
                            return {
                                "url": url,
                                "previewUrl": r.get("previewUrl"),
                                "title": r.get("trackName"),
                                "artist": r.get("artistName"),
                                "album": r.get("collectionName"),
                                "coverUrl": r.get("artworkUrl100").replace("100x100", "400x400") if r.get("artworkUrl100") else None,
                                "releaseDate": r.get("releaseDate"),
                                "isrc": r.get("trackId")
                            }

            # --------- SoundCloud / YouTube Music / Others ---------
            else:
                # fallback to yt-dlp for unknown URLs
                with YoutubeDL(YTDL_EXTRACT_OPTS) as ydl:
                    info = ydl.extract_info(url, download=False)
                return {
                    "url": url,
                    "title": info.get("title"),
                    "artist": info.get("uploader") or info.get("artist") or "Unknown",
                    "album": info.get("album") or "",
                    "coverUrl": info.get("thumbnail"),
                    "releaseDate": info.get("release_date"),
                    "isrc": info.get("isrc", "Unknown")
                }

        except Exception as e:
            logger.exception("Failed to extract metadata from URL: %s", e)
            return None

    @staticmethod
    async def get_links(url: str) -> Dict[str, str]:
        """
        Query Song.link API for cross-platform links for the given URL.
        Returns a map of platform_key -> url (keys lowercased).
        """
        try:
            api_url = "https://api.song.link/v1-alpha.1/links"
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(api_url, params={"url": url})
                res.raise_for_status()
                data = res.json()

            links: Dict[str, str] = {}

            for key, value in data.get("linksByPlatform", {}).items():
                # value may be dict like {"url": "..."}
                if isinstance(value, dict):
                    links[key.lower()] = value.get("url")
                else:
                    links[key.lower()] = value

            if not links:
                links["original"] = url

            return links

        except Exception as e:
            logger.exception("Failed to fetch links from Songlink: %s", e)
            return {"original": url}

    @staticmethod
    async def get_download_link(link) -> str:
        """
        Resolve a downloadable URL for a given track link using Song.link.
        Prefer YouTube links (youtube -> youtube_music) when available because
        yt-dlp can download directly from YouTube. Fall back to other platforms
        supported by yt-dlp. Uses DOWNLOAD_LINKS_CACHE to avoid repeated lookups.
        """
        # Accept either a URL string or an object that contains a "url" key
        if isinstance(link, dict):
            url = link.get("url") or link.get("original") or ""
        else:
            url = str(link or "")

        if not url:
            return ""

        # cache key
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        cached = DOWNLOAD_LINKS_CACHE.get(key)
        if cached:
            return cached

        dlink = await Crawler.get_links(url)
        logger.debug("Song.link result for %s: %s", url, dlink)

        # Normalize keys to prefer youtube
        # Song.link typically uses keys like 'youtube', 'youtube_music', 'spotify', 'deezer', 'apple_music', 'itunes'
        preferred_keys = ["youtube", "youtube_music", "ytmusic", "soundcloud", "spotify", "deezer", "apple_music", "itunes", "original"]

        resolved = None

        # 1) direct youtube
        if dlink.get("youtube"):
            resolved = dlink.get("youtube")

        # 2) youtube_music -> convert to youtube watch URL when possible
        elif dlink.get("youtube_music") or dlink.get("ytmusic"):
            ytm = dlink.get("youtube_music") or dlink.get("ytmusic")
            if ytm:
                # Convert music.youtube.com/watch?v=... to www.youtube.com/watch?v=...
                if "music.youtube.com" in ytm and "watch?v=" in ytm:
                    resolved = ytm.replace("music.youtube.com/watch?v=", "www.youtube.com/watch?v=")
                else:
                    resolved = ytm  # fallback: use as-is

        # 3) other fallbacks
        if not resolved:
            for k in preferred_keys:
                val = dlink.get(k)
                if val:
                    resolved = val
                    break

        # Final fallback: original url
        if not resolved:
            resolved = url

        # Cache and return
        try:
            DOWNLOAD_LINKS_CACHE[key] = resolved
        except Exception:
            # don't break flow on caching issues
            logger.debug("Failed to store download link in cache for %s", url, exc_info=True)

        return resolved
