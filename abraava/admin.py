from django.contrib import admin
from django import forms
from django.urls import path
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib import messages
from .models import User, Playlist, Track, Tag
from django.core.management import call_command

class CrawlItunesForm(forms.Form):
    query = forms.CharField(label="iTunes Search Query", max_length=255)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "email", "user_type", "is_active", "date_joined")
    search_fields = ("name", "email")
    list_filter = ("user_type", "is_active")
    ordering = ("-date_joined",)
    change_list_template = "admin/abraava/user_changelist.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('crawl-itunes/', self.admin_site.admin_view(self.crawl_itunes_view), name='crawl-itunes'),
            path('crawl-itunes-ajax/', self.admin_site.admin_view(self.crawl_itunes_ajax), name='crawl-itunes-ajax'),
            path('latest-tracks/', self.admin_site.admin_view(self.latest_tracks), name='latest-tracks'),
        ]
        return custom_urls + urls

    def crawl_itunes_view(self, request):
        if request.method == "POST":
            form = CrawlItunesForm(request.POST)
            if form.is_valid():
                query = form.cleaned_data["query"]
                try:
                    call_command("crawl_itunes", query=query)
                    self.message_user(request, f"Crawler started for query: {query}", level=messages.SUCCESS)
                except Exception as e:
                    self.message_user(request, f"Error: {e}", level=messages.ERROR)
                return redirect("..")
        else:
            form = CrawlItunesForm()
        context = dict(
            self.admin_site.each_context(request),
            form=form,
        )
        return render(request, "admin/abraava/crawl_itunes.html", context)

    def crawl_itunes_ajax(self, request):
        if request.method == "POST":
            query = request.POST.get("query")
            try:
                call_command("crawl_itunes", query=query)
                return JsonResponse({"status": "ok"})
            except Exception as e:
                return JsonResponse({"status": "error", "error": str(e)}, status=500)
        return JsonResponse({"status": "error", "error": "Invalid request"}, status=400)

    def latest_tracks(self, request):
        tracks = Track.objects.select_related('playlist').order_by('-created_at')[:10]
        data = [
            {
                "id": str(t.id),
                "title": t.title,
                "artist": t.artist_name,
                "album": t.album_name,
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "cover_url": t.playlist.cover_url if t.playlist and t.playlist.cover_url else "",
            }
            for t in tracks
        ]
        return JsonResponse({"tracks": data})

@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "playlist_type", "release_date", "is_public")
    search_fields = ("name", "user__name")
    list_filter = ("playlist_type", "is_public", "release_date")
    ordering = ("-release_date",)

@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "playlist", "artist_name", "album_name", "genre", "release_year", "duration")
    search_fields = ("title", "artist_name", "album_name", "genre")
    list_filter = ("genre", "release_year")
    ordering = ("-release_year",)
    change_list_template = "admin/abraava/track_changelist.html"

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)
