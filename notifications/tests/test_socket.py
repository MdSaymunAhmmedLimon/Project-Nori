import secrets

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from ride_sharing_app.asgi import application


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class WebSocketTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="socket_user", email="socket@example.com", password=secrets.token_urlsafe(24))
        self.token = str(RefreshToken.for_user(self.user).access_token)

    def test_jwt_authentication_and_private_events(self):
        async def scenario():
            socket = WebsocketCommunicator(application, "/ws/events/")
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await socket.receive_json_from())["event"], "authentication_required")
            await get_channel_layer().group_send(f"user_{self.user.pk}", {"type":"app.event", "event":"private"})
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            await socket.send_json_to({"type":"authenticate", "access":self.token})
            self.assertEqual((await socket.receive_json_from())["event"], "connected")
            await get_channel_layer().group_send("user_999999", {"type":"app.event", "event":"other-user"})
            self.assertTrue(await socket.receive_nothing(timeout=0.05))
            await get_channel_layer().group_send(f"user_{self.user.pk}", {"type":"app.event", "event":"ride_updated"})
            self.assertEqual((await socket.receive_json_from())["event"], "ride_updated")
            await socket.disconnect()
        async_to_sync(scenario)()

    def test_invalid_token_and_untrusted_origin_are_rejected(self):
        async def scenario():
            socket = WebsocketCommunicator(application, "/ws/events/")
            await socket.connect()
            await socket.receive_json_from()
            await socket.send_json_to({"type":"authenticate", "access":"invalid"})
            response = await socket.receive_output()
            self.assertEqual(response["type"], "websocket.close")
            self.assertEqual(response["code"], 4401)
            await socket.disconnect()
            foreign = WebsocketCommunicator(application, "/ws/events/", headers=[(b"origin", b"https://untrusted.example")])
            self.assertFalse((await foreign.connect())[0])
            await foreign.disconnect()
        async_to_sync(scenario)()

    def test_inactive_account_loses_live_access(self):
        async def scenario():
            socket = WebsocketCommunicator(application, "/ws/events/")
            await socket.connect()
            await socket.receive_json_from()
            await socket.send_json_to({"type":"authenticate", "access":self.token})
            await socket.receive_json_from()
            await database_sync_to_async(get_user_model().objects.filter(pk=self.user.pk).update)(is_active=False)
            await socket.send_json_to({"type":"ping"})
            self.assertEqual((await socket.receive_output())["code"], 4401)
            await socket.disconnect()
        async_to_sync(scenario)()

    def test_browser_session_is_rechecked_after_logout(self):
        self.client.force_login(self.user)
        cookie = f"sessionid={self.client.cookies['sessionid'].value}".encode()
        async def scenario():
            socket = WebsocketCommunicator(application, "/ws/events/", headers=[(b"origin", b"http://testserver"), (b"cookie", cookie)])
            self.assertTrue((await socket.connect())[0])
            self.assertEqual((await socket.receive_json_from())["event"], "connected")
            await database_sync_to_async(self.client.logout)()
            await socket.send_json_to({"type":"ping"})
            self.assertEqual((await socket.receive_output())["code"], 4401)
            await socket.disconnect()
        async_to_sync(scenario)()

    def test_password_change_stops_events_on_existing_jwt_connection(self):
        def change_password():
            self.user.set_password(secrets.token_urlsafe(24))
            self.user.save(update_fields=["password"])

        async def scenario():
            socket = WebsocketCommunicator(application, "/ws/events/")
            await socket.connect()
            await socket.receive_json_from()
            await socket.send_json_to({"type": "authenticate", "access": self.token})
            self.assertEqual((await socket.receive_json_from())["event"], "connected")
            await database_sync_to_async(change_password)()
            await get_channel_layer().group_send(f"user_{self.user.pk}", {"type": "app.event", "event": "private"})
            response = await socket.receive_output()
            self.assertEqual(response["type"], "websocket.close")
            self.assertEqual(response["code"], 4401)
            await socket.disconnect()
        async_to_sync(scenario)()
