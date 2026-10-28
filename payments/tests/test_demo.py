from rides.tests.routing_fixtures import mock_routing
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from drivers.services import get_driver_balance
from payments.demo import refund_quote
from payments.models import DemoPrepayment, Payment, Wallet, WalletTransaction
from rides.models import Ride, RideQuote, ScheduledRide
from rides.services import dispatch_scheduled_ride, expire_searches, transition_ride
from rides.tests.test_workflow import make_driver


class DemoPaymentTests(APITestCase):
    def setUp(self):
        mock_routing(self)
        cache.clear()
        self.user = get_user_model().objects.create_user(username='wallet_rider', email='wallet@example.com')
        self.other = get_user_model().objects.create_user(username='other_rider', email='other@example.com')
        self.driver = make_driver('wallet_driver')
        self.client.force_authenticate(self.user)
        self.quote = RideQuote.objects.create(rider=self.user, pickup_label='Banani', destination_label='Gulshan',
            pickup_latitude='23.810300', pickup_longitude='90.412500', destination_latitude='23.780600',
            destination_longitude='90.419300', distance_km=3, estimated_minutes=10, fare=Decimal('125.50'),
            platform_fee=Decimal('18.83'), currency='BDT', expires_at=timezone.now()+timedelta(minutes=10))

    def topup(self, **changes):
        data = {'amount':'500.00', 'request_id':str(uuid.uuid4()), 'demo_channel':'BKASH', 'demo_outcome':'SUCCESS'} | changes
        return self.client.post('/api/payments/wallet/top-up/', data, format='json')

    def book(self, **changes):
        data = {'quote_id':str(self.quote.pk), 'request_id':str(uuid.uuid4()), 'payment_method':'WALLET'} | changes
        return self.client.post('/api/rides/', data, format='json')

    def reserve(self, **changes):
        data = {'quote_id':str(self.quote.pk), 'scheduled_for':(timezone.now()+timedelta(hours=2)).isoformat(),
                'payment_method':'WALLET'} | changes
        return self.client.post('/api/rides/schedule/', data, format='json')

    def balance(self):
        return Wallet.objects.get(user=self.user).balance

    def test_wallet_starts_at_zero_and_is_private(self):
        response = self.client.get('/api/payments/wallet/')
        self.assertEqual(response.data['balance'], '0.00')
        self.assertTrue(response.data['is_demo'])
        self.assertEqual(self.topup().status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/payments/wallet/').data['balance'], '0.00')
        self.assertEqual(self.client.get('/api/payments/wallet/transactions/').data['count'], 0)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.topup().status_code, 403)
        self.client.force_authenticate(None)
        for url in ['/api/payments/wallet/', '/api/payments/wallet/transactions/']:
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_topup_idempotency_and_history(self):
        key = str(uuid.uuid4())
        first = self.topup(request_id=key)
        again = self.topup(request_id=key)
        self.assertEqual(first.data, again.data)
        self.assertEqual(self.balance(), Decimal('500.00'))
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.assertEqual(self.topup(request_id=key, amount='600').status_code, 409)
        self.assertEqual(self.topup(request_id=key, demo_channel='NAGAD').status_code, 409)
        self.assertEqual(self.client.get('/api/payments/wallet/transactions/').data['count'], 1)

    def test_failed_cancelled_and_invalid_topups_never_credit(self):
        for fields in [{'demo_outcome':'FAILED'}, {'demo_outcome':'CANCELLED'}, {'amount':'-1'},
                       {'amount':'10001'}, {'amount':'NaN'}, {'amount':'10.001'}, {'demo_channel':'UNKNOWN'},
                       {'balance':'9999'}, {'currency':'USD'}]:
            with self.subTest(fields=fields):
                self.assertEqual(self.topup(**fields).status_code, 400)
        self.assertFalse(WalletTransaction.objects.exists())

    def test_wallet_prepaid_once_and_cancel_refunded_once(self):
        self.topup()
        key = str(uuid.uuid4())
        response = self.book(request_id=key)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['payment']['status'], 'PAID')
        self.assertTrue(response.data['payment']['is_demo'])
        self.assertEqual(self.book(request_id=key).status_code, 200)
        self.assertEqual(self.balance(), Decimal('374.50'))
        ride = Ride.objects.get(pk=response.data['id'])
        transition_ride(self.user, ride.pk, 'cancel')
        transition_ride(self.user, ride.pk, 'cancel')
        refund_quote(self.quote.pk)
        self.assertEqual(self.balance(), Decimal('500.00'))
        self.assertEqual(WalletTransaction.objects.filter(kind='REFUND').count(), 1)
        self.assertEqual(Payment.objects.get(ride=ride).status, 'REFUNDED')

    def test_insufficient_wallet_rollback_and_tampering(self):
        response = self.book()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Ride.objects.exists())
        self.assertFalse(DemoPrepayment.objects.exists())
        self.assertEqual(self.book(amount='1').status_code, 400)
        self.topup()
        self.assertEqual(self.book(promo_code='DEMO').status_code, 400)
        self.assertEqual(self.balance(), Decimal('500.00'))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.book().status_code, 404)

    def test_expired_quote_does_not_debit_wallet(self):
        self.topup()
        RideQuote.objects.filter(pk=self.quote.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.book().status_code, 400)
        self.assertEqual(self.balance(), Decimal('500.00'))

    def test_wallet_currency_mismatch(self):
        self.topup()
        Wallet.objects.filter(user=self.user).update(currency='USD')
        self.assertEqual(self.book().status_code, 409)
        self.assertFalse(DemoPrepayment.objects.exists())

    def test_ssl_demo_outcomes_and_server_pricing(self):
        for outcome in ['FAILED', 'CANCELLED']:
            response = self.book(payment_method='SSL_DEMO', demo_channel='NAGAD', demo_outcome=outcome)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertFalse(Ride.objects.exists())
        self.assertEqual(self.book(payment_method='SSL_DEMO').status_code, 400)
        response = self.book(payment_method='SSL_DEMO', demo_channel='NAGAD', demo_outcome='SUCCESS')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['payment']['amount'], '125.50')
        self.assertEqual(DemoPrepayment.objects.get().channel, 'NAGAD')
        self.assertFalse(WalletTransaction.objects.exists())
        transition_ride(self.user, response.data['id'], 'cancel')
        self.assertEqual(DemoPrepayment.objects.get().status, 'REFUNDED')

    def test_reservation_cancel_returns_prepaid_credits(self):
        self.topup()
        pickup = (timezone.now()+timedelta(hours=2)).isoformat()
        response = self.reserve(scheduled_for=pickup)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.reserve(scheduled_for=pickup).data['id'], response.data['id'])
        self.assertEqual(self.balance(), Decimal('374.50'))
        url = f"/api/rides/schedule/{response.data['id']}/"
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.balance(), Decimal('500.00'))

    def test_scheduled_dispatch_reuses_prepayment_after_quote_expiry(self):
        self.topup()
        response = self.reserve()
        sr = ScheduledRide.objects.get(pk=response.data['id'])
        ScheduledRide.objects.filter(pk=sr.pk).update(scheduled_for=timezone.now()-timedelta(seconds=1))
        RideQuote.objects.filter(pk=self.quote.pk).update(expires_at=timezone.now()-timedelta(minutes=10))
        ride = dispatch_scheduled_ride(sr.pk)
        self.assertIsNotNone(ride)
        self.assertEqual(ride.payment.status, 'PAID')
        self.assertIsNone(dispatch_scheduled_ride(sr.pk))
        self.assertEqual(self.balance(), Decimal('374.50'))
        self.assertEqual(WalletTransaction.objects.filter(kind='RIDE').count(), 1)

    def test_missed_reservation_and_search_timeout_refund(self):
        self.topup()
        response = self.reserve()
        sr = ScheduledRide.objects.get(pk=response.data['id'])
        ScheduledRide.objects.filter(pk=sr.pk).update(scheduled_for=timezone.now()-timedelta(minutes=20))
        self.assertIsNone(dispatch_scheduled_ride(sr.pk))
        self.assertEqual(self.balance(), Decimal('500.00'))
        self.quote.pk = uuid.uuid4()
        self.quote.save(force_insert=True)
        response = self.book()
        Ride.objects.filter(pk=response.data['id']).update(created_at=timezone.now()-timedelta(hours=1))
        expire_searches(self.user)
        expire_searches(self.user)
        self.assertEqual(self.balance(), Decimal('500.00'))

    def test_demo_completed_trip_has_receipt_and_no_real_earnings(self):
        response = self.book(payment_method='SSL_DEMO', demo_channel='CARD', demo_outcome='SUCCESS')
        ride = Ride.objects.get(pk=response.data['id'])
        ride.driver = self.driver
        ride.status = 'IN_PROGRESS'
        ride.save()
        transition_ride(self.driver, ride.pk, 'complete')
        self.assertEqual(Payment.objects.filter(ride=ride).count(), 1)
        self.assertEqual(get_driver_balance(self.driver), Decimal('0'))
        self.client.force_login(self.user)
        receipt = self.client.get(f'/app/rides/{ride.pk}/receipt/')
        self.assertContains(receipt, 'DEMO RECEIPT')
        rating_url = f'/api/rides/{ride.pk}/rating/'
        self.assertEqual(self.client.post(rating_url, {'score':6}, format='json').status_code, 400)
        self.assertEqual(self.client.post(rating_url, {'score':5, 'feedback_tags':['Friendly','On time']}, format='json').status_code, 201)
        self.assertEqual(self.client.post(rating_url, {'score':5}, format='json').status_code, 409)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.get('/api/payments/earnings/').data['totals'], [])
        self.assertEqual(self.client.post(f'/api/payments/{ride.pk}/cash/', {}).status_code, 409)
