from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower



class UserManager(DjangoUserManager):
    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email(email).strip().lower()

    def _create_user_object(self, username, email, password, **extra_fields):
        email = self.normalize_email(email)
        if not email:
            raise ValueError("An email address is required.")
        return super()._create_user_object(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        RIDER = "RIDER", "Rider"
        DRIVER = "DRIVER", "Driver"

    SECURITY_QUESTION_CHOICES = [
        ("What was the name of your first pet?", "What was the name of your first pet?"),
        ("In what city were you born?", "In what city were you born?"),
        ("What was the make of your first car?", "What was the make of your first car?"),
        ("What was your childhood nickname?", "What was your childhood nickname?"),
        ("What is your mother's maiden name?", "What is your mother's maiden name?"),
    ]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=6, choices=Role.choices, default=Role.RIDER)
    phone_number = models.CharField(
        max_length=16,
        blank=True,
        validators=[
            RegexValidator(
                r"^\+[1-9]\d{7,14}$",
                "Use an international phone number, e.g. +8801712345678.",
            )
        ],
    )
    security_question = models.CharField(
        max_length=255,
        blank=True,
        choices=SECURITY_QUESTION_CHOICES,
        help_text="Security question for password recovery without email.",
    )
    security_answer = models.CharField(
        max_length=255,
        blank=True,
        help_text="Hashed answer to the security question.",
    )
    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique"),
            models.CheckConstraint(
                condition=models.Q(role__in=["RIDER", "DRIVER"]),
                name="accounts_user_valid_role",
            ),
            models.CheckConstraint(
                condition=~models.Q(email=""), name="accounts_user_email_not_empty"
            ),
        ]

    def set_security_answer(self, raw_answer):
        from django.contrib.auth.hashers import make_password
        if raw_answer:
            self.security_answer = make_password(raw_answer.strip().lower())
        else:
            self.security_answer = ""

    def check_security_answer(self, raw_answer):
        from django.contrib.auth.hashers import check_password
        if not self.security_answer or not raw_answer:
            return False
        return check_password(raw_answer.strip().lower(), self.security_answer)

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        super().save(*args, **kwargs)


class ReferralCode(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_code"
    )
    code = models.CharField(max_length=15, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.code})"


class Referral(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending (awaiting first ride)"
        COMPLETED = "COMPLETED", "Completed (rewarded)"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made"
    )
    referee = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reward_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("50.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer.username} -> {self.referee.username} ({self.get_status_display()})"

