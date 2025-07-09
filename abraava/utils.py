from django.core.mail import send_mail
from django.urls import reverse

from abraava.models import EmailVerificationToken


def send_verification_email(user):
    token_obj, created = EmailVerificationToken.objects.get_or_create(user=user)
    verification_link = f"https://yourdomain.com/verify-email/{token_obj.token}/"
    send_mail(
        subject="Verify your email",
        message=f"Please click the link to verify your email: {verification_link}",
        from_email="noreply@yourdomain.com",
        recipient_list=[user.email],
    )
