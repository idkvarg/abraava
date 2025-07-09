import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone
from datetime import timedelta

# --- Custom User Manager ---
class CustomUserManager(BaseUserManager):
    def create_user(self, name, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(name=name, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, name, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(name, email, password, **extra_fields)


# --- User Model ---
class User(AbstractBaseUser, PermissionsMixin):
    USER_TYPE_CHOICES = [
        ("regular", "Regular User"),
        ("artist", "Artist"),
        ("admin", "Admin"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    email = models.EmailField(unique=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default="regular")

    profile_picture = models.URLField(blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    website = models.URLField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.name


# --- Tag Model ---
class Tag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


# --- Playlist / Album / EP Model ---
class Playlist(models.Model):
    PLAYLIST_TYPE_CHOICES = [
        ("album", "Album"),
        ("playlist", "Playlist"),
        ("ep", "EP"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="playlists")
    playlist_type = models.CharField(max_length=20, choices=PLAYLIST_TYPE_CHOICES, default="album")
    release_date = models.DateField(blank=True, null=True)
    cover_url = models.URLField(blank=True, null=True)
    is_public = models.BooleanField(default=True)
    tags = models.ManyToManyField(Tag, blank=True, related_name="playlists")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.get_playlist_type_display()})"


# --- Track Model ---
class Track(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="tracks")
    title = models.CharField(max_length=255)
    duration = models.IntegerField(help_text="Duration in seconds", null=True, blank=True)
    track_number = models.IntegerField(null=True, blank=True)
    audio_url = models.URLField(blank=True, null=True)
    album_name = models.CharField(max_length=255, blank=True, null=True)
    genre = models.CharField(max_length=100, blank=True, null=True)
    release_year = models.PositiveIntegerField(blank=True, null=True)
    bitrate = models.PositiveIntegerField(help_text="Bitrate in kbps", blank=True, null=True)
    audio_format = models.CharField(max_length=20, blank=True, null=True)
    composer = models.CharField(max_length=255, blank=True, null=True)
    lyrics = models.TextField(blank=True, null=True)
    disc_number = models.PositiveIntegerField(blank=True, null=True)
    comments = models.TextField(blank=True, null=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name="tracks")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.track_number or '?'} - {self.title}"


# --- Unified UserAction Model ---
class UserAction(models.Model):
    ACTION_TYPE_CHOICES = [
        ("like", "Like"),
        ("comment", "Comment"),
        ("play", "Play"),
        ("follow", "Follow"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="actions")
    action_type = models.CharField(max_length=20, choices=ACTION_TYPE_CHOICES)

    # فقط یکی از این‌ها در هر رکورد می‌تونه ست بشه
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="actions", null=True, blank=True)
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="actions", null=True, blank=True)
    target_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="followers_actions", null=True, blank=True)

    comment_text = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "action_type"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "target_user", "action_type"], condition=models.Q(action_type="follow"), name="unique_user_follow")
        ]

    def __str__(self):
        if self.action_type == "follow" and self.target_user:
            return f"{self.user.name} followed {self.target_user.name}"
        elif self.track:
            return f"{self.user.name} {self.action_type} track {self.track.title}"
        elif self.playlist:
            return f"{self.user.name} {self.action_type} playlist {self.playlist.name}"
        return f"{self.user.name} {self.action_type}"



class EmailVerificationToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='email_verification_token')
    token = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expired_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if not self.expired_at:
            self.expired_at = timezone.now() + timedelta(days=1)
        super().save(*args, **kwargs)