from .models import User
from rest_framework import serializers,generics, permissions

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('id', 'name', 'email', 'password', 'user_type', 'profile_picture')

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)

from .models import Playlist, Track, Tag, UserAction, User


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class TrackSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    class Meta:
        model = Track
        fields = "__all__"


class PlaylistSerializer(serializers.ModelSerializer):
    tracks = TrackSerializer(many=True, read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Playlist
        fields = "__all__"


class UserActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAction
        fields = "__all__"


class UserPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "profile_picture", "user_type"]

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'email', 'profile_picture', 'bio', 'website']
        read_only_fields = ['email', 'name']  # اگه نمیخوای تغییر بدن


class UserProfileUpdateView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
