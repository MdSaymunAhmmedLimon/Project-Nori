import secrets
import string

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Referral, ReferralCode


def generate_unique_referral_code(user):
    base = "".join(c for c in user.username.upper() if c.isalnum())[:6] or "NORI"
    suffix = "".join(secrets.choice(string.digits) for _ in range(4))
    code = f"{base}{suffix}"
    while ReferralCode.objects.filter(code=code).exists():
        suffix = "".join(secrets.choice(string.digits) for _ in range(4))
        code = f"{base}{suffix}"
    return code


@transaction.atomic
def register_user(*, password, referral_code="", **user_data):
    user = get_user_model().objects.create_user(password=password, **user_data)
    code = generate_unique_referral_code(user)
    ReferralCode.objects.get_or_create(user=user, defaults={"code": code})

    if referral_code:
        ref = ReferralCode.objects.filter(code__iexact=referral_code.strip()).first()
        if ref and ref.user_id != user.id:
            Referral.objects.get_or_create(referee=user, defaults={"referrer": ref.user})
    return user
