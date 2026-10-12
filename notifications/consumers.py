import asyncio
import time
from importlib import import_module
from types import SimpleNamespace
from urllib.parse import urlsplit

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user
from django.http.request import validate_host
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.exceptions import AuthenticationFailed


class EventsConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user_id, self.timeout_task, self.token_expiry = None, None, None
        self.session_key = None
        self.validated_token = None
        origin = dict(self.scope.get("headers", [])).get(b"origin")
        if origin:
            parsed = urlsplit(origin.decode())
            if parsed.scheme not in {"http", "https"} or not validate_host(parsed.hostname or "", settings.ALLOWED_HOSTS):
                await self.close(code=4403)
                return
        await self.accept()
        user = self.scope.get("user")
        if origin and user and user.is_authenticated and user.is_active:
            self.session_key = self.scope["session"].session_key
            await self.authenticate(user.pk)
        else:
            # Native clients authenticate in their first frame, never in a URL.
            await self.send_json({"event": "authentication_required"})
            self.timeout_task = asyncio.create_task(self.authentication_timeout())

    async def authentication_timeout(self):
        await asyncio.sleep(10)
        await self.close(code=4401)

    async def authenticate(self, user_id):
        self.user_id = user_id
        await self.channel_layer.group_add(f"user_{user_id}", self.channel_name)
        if self.timeout_task:
            self.timeout_task.cancel()
        await self.send_json({"event": "connected"})

    @database_sync_to_async
    def token_user(self, token):
        auth = JWTAuthentication()
        validated = auth.get_validated_token(token)
        self.validated_token = validated
        return auth.get_user(validated).pk, validated["exp"]

    @database_sync_to_async
    def active_user(self):
        if self.token_expiry and time.time() >= self.token_expiry:
            return False
        if self.session_key:
            store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=self.session_key)
            user = get_user(SimpleNamespace(session=store))
            return user.is_authenticated and user.is_active and user.pk == self.user_id
        try:
            return JWTAuthentication().get_user(self.validated_token).pk == self.user_id
        except (InvalidToken, AuthenticationFailed):
            return False

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self.close(code=4400)
            return
        if not self.user_id:
            try:
                if content.get("type") != "authenticate" or not isinstance(content.get("access"), str):
                    raise ValueError()
                user_id, self.token_expiry = await self.token_user(content["access"])
            except (InvalidToken, TokenError, AuthenticationFailed, ValueError, TypeError):
                await self.close(code=4401)
                return
            await self.authenticate(user_id)
        elif content.get("type") == "ping":
            if not await self.active_user():
                await self.close(code=4401)
            else:
                await self.send_json({"event": "pong"})

    async def app_event(self, event):
        if self.user_id and await self.active_user():
            await self.send_json({key: value for key, value in event.items() if key != "type"})
        else:
            await self.close(code=4401)

    async def disconnect(self, close_code):
        if self.timeout_task:
            self.timeout_task.cancel()
        if self.user_id:
            await self.channel_layer.group_discard(f"user_{self.user_id}", self.channel_name)
