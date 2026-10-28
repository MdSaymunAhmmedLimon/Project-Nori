import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from rides.models import Ride, RideMessage, RideQuote
from rides.tests.test_workflow import make_driver


class LiveTripTests(APITestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.rider = get_user_model().objects.create_user(username='live-rider', email='live@example.com')
        self.stranger = get_user_model().objects.create_user(username='stranger', email='stranger@example.com')
        self.driver = make_driver('live-driver')
        quote = RideQuote.objects.create(rider=self.rider, pickup_label='A', destination_label='B',
            pickup_latitude='23.810300', pickup_longitude='90.412500',
            destination_latitude='23.780600', destination_longitude='90.419300',
            distance_km='4.29', estimated_minutes=8, fare='150', platform_fee='22.50',
            currency='BDT', expires_at=timezone.now() + timedelta(minutes=10))
        self.ride = Ride.objects.create(rider=self.rider, driver=self.driver, quote=quote,
            status=Ride.Status.ACCEPTED, request_id=uuid.uuid4())
        self.url = f'/api/rides/{self.ride.pk}/messages/'
        self.client.force_authenticate(self.rider)

    @patch('rides.services.publish')
    def test_two_way_messaging_and_live_event(self, publish):
        result = self.client.post(self.url, {'content': '  At the entrance  '})
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['content'], 'At the entrance')
        publish.assert_called_with(self.driver.pk, 'chat_message', ride_id=str(self.ride.pk),
                                   content='At the entrance', sender=self.rider.username)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.post(self.url, {'content': 'On my way'}).status_code, 201)
        messages = self.client.get(self.url).data
        self.assertEqual([item['content'] for item in messages], ['At the entrance', 'On my way'])

    def test_private_messages_are_only_available_to_participants(self):
        self.client.post(self.url, {'content': 'Private pickup details'})
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'content': 'intrusion'}).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_unassigned_or_finished_trip_cannot_send(self):
        for status in ('SEARCHING', 'COMPLETED', 'CANCELLED'):
            self.ride.status = status
            self.ride.save()
            self.assertEqual(self.client.post(self.url, {'content': 'Hello'}).status_code, 409)
        self.assertFalse(RideMessage.objects.exists())

    def test_finished_trip_preserves_message_history(self):
        self.client.post(self.url, {'content': 'Thanks'})
        self.ride.status = 'COMPLETED'
        self.ride.save()
        self.assertEqual(self.client.get(self.url).data[0]['content'], 'Thanks')

    def test_empty_and_overlong_messages_are_rejected(self):
        for content in ('   ', 'a' * 501):
            self.assertEqual(self.client.post(self.url, {'content': content}).status_code, 400)
        self.assertFalse(RideMessage.objects.exists())

    def test_driver_movement_reaches_rider_and_is_private(self):
        self.client.force_authenticate(self.driver)
        location = {'latitude': '23.809100', 'longitude': '90.411200'}
        result = self.client.post('/api/drivers/location/', location)
        self.assertEqual(result.status_code, 200, result.data)
        self.client.force_authenticate(self.rider)
        detail = self.client.get(f'/api/rides/{self.ride.pk}/')
        self.assertEqual(detail.data['driver']['location']['latitude'], location['latitude'])
        self.assertEqual(detail.data['driver']['location']['longitude'], location['longitude'])
        self.assertEqual(self.ride.track_points.count(), 1)
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(f'/api/rides/{self.ride.pk}/tracking/').status_code, 404)
