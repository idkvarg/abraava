from rest_framework.permissions import BasePermission

class IsArtist(BasePermission):
    def has_permission(self, request, view):
        return request.user.user_type == 'artist'
