from datetime import timezone

from rest_framework import generics, viewsets, permissions
from rest_framework.permissions import AllowAny
from .models import Playlist, Track, UserAction, Tag, User, EmailVerificationToken
from .serializers import RegisterSerializer, PlaylistSerializer, TrackSerializer, UserActionSerializer, TagSerializer, \
    UserPublicSerializer


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer


class PlaylistViewSet(viewsets.ModelViewSet):
    queryset = Playlist.objects.filter(is_public=True).select_related("user").prefetch_related("tracks", "tags")
    serializer_class = PlaylistSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    filterset_fields = ["playlist_type", "user"]
    search_fields = ["name", "description", "tags__name"]
    ordering_fields = ["release_date", "created_at"]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TrackViewSet(viewsets.ModelViewSet):
    queryset = Track.objects.select_related("playlist").prefetch_related("tags")
    serializer_class = TrackSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    filterset_fields = ["playlist", "genre", "release_year"]
    search_fields = ["title", "artist_name", "album_name", "composer", "genre", "tags__name"]
    ordering_fields = ["track_number", "release_year", "duration"]


class UserActionViewSet(viewsets.ModelViewSet):
    queryset = UserAction.objects.all()
    serializer_class = UserActionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserPublicSerializer
    permission_classes = [permissions.AllowAny]
    search_fields = ["name", "email", "bio"]

from django.shortcuts import get_object_or_404
from django.http import JsonResponse

def verify_email(request, token):
    token_obj = get_object_or_404(EmailVerificationToken, token=token)
    if timezone.now() > token_obj.expired_at:
        return JsonResponse({"detail": "Token expired"}, status=400)

    user = token_obj.user
    user.is_active = True
    user.save()
    token_obj.delete()  # توکن حذف میشه بعد از تایید
    return JsonResponse({"detail": "Email verified successfully"})