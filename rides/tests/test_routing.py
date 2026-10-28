import io
import json
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from rides.models import RideQuote
from rides.routing import RoutingUnavailable, get_pickup_distances, get_road_route
from rides.tests.routing_fixtures import mock_routing
from rides.tests.test_workflow import make_driver
from django.contrib.auth import get_user_model


POINTS = [(23.8103, 90.4125), (23.7806, 90.4193)]
GEOMETRY = {'type': 'LineString', 'coordinates': [[90.4125, 23.8103], [90.415, 23.8], [90.4193, 23.7806]]}


def response(distance=8750, duration=1021, **changes):
    payload = {'code': 'Ok', 'routes': [{'distance': distance, 'duration': duration, 'geometry': GEOMETRY}]}
    return io.BytesIO(json.dumps(payload | changes).encode())


@override_settings(ROUTING_BASE_URL='https://routing.example', ROUTING_TIMEOUT_SECONDS=3)
class RoutingClientTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @patch('rides.routing.urlopen')
    def test_coordinates_order_units_and_cache(self, fetch):
        fetch.return_value = response()
        result = get_road_route(POINTS)
        self.assertEqual(result, {'distance_meters': 8750, 'duration_seconds': 1021, 'geometry': GEOMETRY})
        self.assertEqual(get_road_route(POINTS), result)
        fetch.assert_called_once()
        request = fetch.call_args.args[0]
        self.assertIn('/90.412500,23.810300;90.419300,23.780600?', request.full_url)
        self.assertEqual(fetch.call_args.kwargs['timeout'], 3)
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)['geometries'], ['geojson'])

    @patch('rides.routing.urlopen')
    def test_unreachable_route_including_http_400(self, fetch):
        for code in ('NoRoute', 'NoSegment'):
            for http_error in (False, True):
                with self.subTest(code=code, http_error=http_error):
                    body = response(code=code)
                    fetch.side_effect = HTTPError('https://routing.example', 400, 'No route', {}, body) if http_error else None
                    fetch.return_value = body
                    with self.assertRaises(ValidationError):
                        get_road_route(POINTS)

    @patch('rides.routing.urlopen')
    def test_service_failures_never_fall_back_to_straight_line(self, fetch):
        for error in (URLError('offline'), TimeoutError('timeout')):
            fetch.side_effect = error
            with self.assertRaises(RoutingUnavailable):
                get_road_route(POINTS)
        fetch.side_effect = None
        for body in (b'invalid json', b'{}', b'null'):
            fetch.return_value = io.BytesIO(body)
            with self.assertRaises(RoutingUnavailable):
                get_road_route(POINTS)
        for distance in (-1, float('nan'), float('inf')):
            fetch.return_value = response(distance=distance)
            with self.assertRaises(RoutingUnavailable):
                get_road_route(POINTS)

    @patch('rides.routing.urlopen')
    def test_invalid_coordinates_never_call_provider(self, fetch):
        for points in ([], POINTS[:1], [(91, 0), (0, 0)], [(float('nan'), 0), (0, 0)], [('bad', 0), (0, 0)]):
            with self.assertRaises(ValidationError):
                get_road_route(points)
        fetch.assert_not_called()

    @patch('rides.routing.urlopen')
    def test_driver_matrix_direction_and_unreachable_driver(self, fetch):
        fetch.return_value = response(distances=[[4200], [None]])
        self.assertEqual(get_pickup_distances(POINTS, (23.8, 90.4)), [4.2, None])
        query = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query)
        self.assertEqual(query['sources'], ['0;1'])
        self.assertEqual(query['destinations'], ['2'])
        self.assertNotIn('fallback_speed', query)

    @patch('rides.routing.urlopen')
    def test_unsnappable_driver_does_not_hide_reachable_driver(self, fetch):
        fetch.side_effect = [response(code='NoSegment'), response(code='NoSegment'), response(distance=4200)]
        self.assertEqual(get_pickup_distances(POINTS, (23.8, 90.4)), [None, 4.2])

    @patch('rides.routing.urlopen')
    def test_matrix_outage_is_reported(self, fetch):
        fetch.side_effect = URLError('offline')
        with self.assertRaises(RoutingUnavailable):
            get_pickup_distances(POINTS, (23.8, 90.4))


class RoadQuoteTests(APITestCase):
    def setUp(self):
        self.http = mock_routing(self)
        self.rider = get_user_model().objects.create_user(username='road-rider', email='road@example.com')
        self.client.force_authenticate(self.rider)
        self.route = {'pickup_label': 'A', 'destination_label': 'B',
                      'pickup_latitude': '23.810300', 'pickup_longitude': '90.412500',
                      'destination_latitude': '23.780600', 'destination_longitude': '90.419300'}
        self.http.side_effect = lambda *args, **kwargs: response()

    @override_settings(FARE_BASE='50', FARE_PER_KM='22', FARE_MINIMUM='80')
    def test_quote_fare_duration_and_geometry_use_same_road_route(self):
        result = self.client.post('/api/rides/quotes/', self.route)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['distance_km'], '8.75')
        self.assertEqual(result.data['estimated_minutes'], 18)
        self.assertEqual(result.data['fare'], '242.50')
        quote = RideQuote.objects.get()
        self.assertEqual(quote.route_geometry, GEOMETRY)
        self.assertEqual(result.data['route_geometry'], GEOMETRY)

    def test_stops_are_routed_in_order(self):
        stop = {'label': 'Stop', 'latitude': '23.800000', 'longitude': '90.400000'}
        result = self.client.post('/api/rides/quotes/', self.route | {'stops': [stop]}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertIn('90.412500,23.810300;90.400000,23.800000;90.419300,23.780600',
                      self.http.call_args.args[0].full_url)
        self.assertEqual(result.data['stops'][0]['label'], 'Stop')

    def test_quote_rejects_short_or_overlong_road_routes(self):
        for meters in (99, 300001):
            cache.clear()
            self.http.side_effect = lambda *args, **kwargs: response(distance=meters)
            result = self.client.post('/api/rides/quotes/', self.route)
            self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(RideQuote.objects.exists())

    def test_outage_or_unreachable_route_does_not_create_quote(self):
        self.http.side_effect = URLError('offline')
        self.assertEqual(self.client.post('/api/rides/quotes/', self.route).status_code, 503)
        self.http.side_effect = lambda *args, **kwargs: response(code='NoRoute')
        self.assertEqual(self.client.post('/api/rides/quotes/', self.route).status_code, 400)
        self.assertFalse(RideQuote.objects.exists())

    def test_preview_validation_and_geometry(self):
        data = {'coordinates': [{'latitude': lat, 'longitude': lon} for lat, lon in POINTS]}
        result = self.client.post('/api/rides/route/', data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['geometry'], GEOMETRY)
        self.assertEqual(self.client.post('/api/rides/route/', {'coordinates': []}, format='json').status_code, 400)

    def test_driver_offer_uses_road_distance_and_live_eta_uses_router(self):
        import uuid
        from rides.models import Ride, RideOffer
        from rides.services import dispatch_ride, get_ride_eta

        driver = make_driver('road-driver')
        quote_data = self.client.post('/api/rides/quotes/', self.route).data
        ride = Ride.objects.create(rider=self.rider, quote_id=quote_data['id'], request_id=uuid.uuid4(), status='SEARCHING')
        self.http.side_effect = lambda *args, **kwargs: response(distances=[[6300]])
        dispatch_ride(ride.pk)
        self.assertEqual(RideOffer.objects.get(ride=ride).distance_km, Decimal('6.30'))
        ride.driver = driver
        ride.status = 'ACCEPTED'
        ride.save()
        eta = get_ride_eta(self.rider, ride.pk)
        self.assertEqual(eta['remaining_km'], '8.75')
        self.assertEqual(eta['estimated_minutes'], 18)

    def test_booking_survives_dispatch_routing_outage_and_retries(self):
        import uuid
        from rides.models import Ride
        from rides.services import dispatch_ride

        make_driver('retry-driver')
        quote = self.client.post('/api/rides/quotes/', self.route).data
        self.http.side_effect = URLError('offline')
        result = self.client.post('/api/rides/', {'quote_id': quote['id'], 'request_id': str(uuid.uuid4())})
        self.assertEqual(result.status_code, 201, result.data)
        ride = Ride.objects.get(pk=result.data['id'])
        self.assertEqual(ride.status, 'SEARCHING')
        self.assertFalse(ride.offers.exists())
        self.http.side_effect = lambda *args, **kwargs: response(distances=[[1200]])
        dispatch_ride(ride.pk)
        self.assertEqual(ride.offers.count(), 1)
