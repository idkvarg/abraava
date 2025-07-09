import requests
from django.core.management.base import BaseCommand
from abraava.models import User, Playlist, Track
from django.db import transaction

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"


def fetch_itunes_artists(query, limit=100):
    params = {
        "term": query,
        "media": "music",
        "entity": "musicArtist",
        "limit": limit,
    }
    resp = requests.get(ITUNES_SEARCH_URL, params=params, timeout=10)
    if resp.status_code == 200:
        return resp.json().get("results", [])
    return []


def fetch_artist_albums(artist_id, limit=200):
    params = {"id": artist_id, "entity": "album", "limit": limit}
    resp = requests.get(ITUNES_LOOKUP_URL, params=params, timeout=10)
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        return [r for r in results if r.get("wrapperType") == "collection"]
    return []


def fetch_album_tracks(album_id, limit=200):
    params = {"id": album_id, "entity": "song", "limit": limit}
    resp = requests.get(ITUNES_LOOKUP_URL, params=params, timeout=10)
    if resp.status_code == 200:
        results = resp.json().get("results", [])
        return [r for r in results if r.get("wrapperType") == "track"]
    return []


class Command(BaseCommand):
    help = "Crawl iTunes Search API and populate artists, albums, and tracks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--query", type=str, help="Search query for iTunes (overrides default queries)"
        )

    def handle(self, *args, **options):
        query = options.get("query")
        if not query:
            self.stdout.write(self.style.ERROR("No query provided."))
            return

        artists = fetch_itunes_artists(query)
        for artist in artists:
            artist_id = artist.get("artistId")
            artist_name = artist.get("artistName")
            if not artist_id or not artist_name:
                continue
            user = None
            try:
                user = User.objects.get(id=artist_id)
                user.name = artist_name
                user.email = f"{artist_name.replace(' ', '').lower()}@itunes.fake"
                user.user_type = "artist"
                user.is_active = True
                user.save()
            except User.DoesNotExist:
                # Try to find by email (to avoid unique constraint error)
                email = f"{artist_name.replace(' ', '').lower()}@itunes.fake"
                try:
                    user = User.objects.get(email=email)
                    user.id = artist_id
                    user.name = artist_name
                    user.user_type = "artist"
                    user.is_active = True
                    user.save()
                except User.DoesNotExist:
                    user = User.objects.create(
                        id=artist_id,
                        name=artist_name,
                        email=email,
                        user_type="artist",
                        is_active=True,
                    )
            self.stdout.write(f"  Crawling albums for artist: {artist_name} ({artist_id})")
            albums = fetch_artist_albums(artist_id)
            for album in albums:
                album_id = album.get("collectionId")
                album_name = album.get("collectionName")
                if not album_id or not album_name:
                    continue
                with transaction.atomic():
                    playlist, _ = Playlist.objects.update_or_create(
                        id=album_id,
                        defaults={
                            "name": album_name,
                            "user": user,
                            "playlist_type": "album",
                            "release_date": album.get("releaseDate", "")[:10] or None,
                            "cover_url": album.get("artworkUrl100"),
                            "is_public": True,
                        },
                    )
                self.stdout.write(f"    Crawling tracks for album: {album_name} ({album_id})")
                tracks = fetch_album_tracks(album_id)
                for track in tracks:
                    track_id = track.get("trackId")
                    track_title = track.get("trackName")
                    if not track_id or not track_title:
                        continue
                    with transaction.atomic():
                        Track.objects.update_or_create(
                            id=track_id,
                            defaults={
                                "title": track_title,
                                "playlist": playlist,
                                "duration": track.get("trackTimeMillis", 0) // 1000
                                if track.get("trackTimeMillis")
                                else None,
                                "track_number": track.get("trackNumber"),
                                "audio_url": track.get("previewUrl"),
                                "artist_name": track.get("artistName"),
                                "album_name": track.get("collectionName"),
                                "genre": track.get("primaryGenreName"),
                                "release_year": int(track.get("releaseDate", "")[:4])
                                if track.get("releaseDate")
                                else None,
                            },
                        )
        self.stdout.write(self.style.SUCCESS("Finished crawling for query: %s" % query))