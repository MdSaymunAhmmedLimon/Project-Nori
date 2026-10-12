from django.contrib.auth import get_user_model
from django.db import transaction


@transaction.atomic
def register_user(*, password, **user_data):
    return get_user_model().objects.create_user(password=password, **user_data)
