from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .serializers import UserProfileUpdateView
from .views import RegisterView, PlaylistViewSet, TrackViewSet, UserActionViewSet, UserViewSet, verify_email

router = DefaultRouter()
router.register(r'playlists', PlaylistViewSet)
router.register(r'tracks', TrackViewSet)
router.register(r'actions', UserActionViewSet)
router.register(r'users', UserViewSet)
urlpatterns = [
    path('auth/register/', RegisterView.as_view(), name='auth_register'),
    path('', include(router.urls)),
    path('auth/verify-email/<uuid:token>/', verify_email, name='verify_email'),
    path('auth/profile/', UserProfileUpdateView.as_view(), name='profile_update'),
]
