from rest_framework import permissions


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    فقط صاحب object می‌تواند آن را ویرایش یا حذف کند.
    """

    def has_object_permission(self, request, view, obj):
        # خواندن مجازه برای همه
        if request.method in permissions.SAFE_METHODS:
            return True

        # ویرایش/حذف فقط برای صاحبش
        return obj.user == request.user
