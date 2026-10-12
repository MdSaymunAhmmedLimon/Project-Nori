from django.apps import AppConfig


class RidesConfig(AppConfig):
    name = "rides"

    def ready(self):
        from . import checks  # noqa: F401
