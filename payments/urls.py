from django.urls import path

from . import views

app_name = "payments"
urlpatterns = [
    path("webhook/stripe/", views.StripeWebhook.as_view(), name="stripe-webhook"),
    path("earnings/", views.Earnings.as_view(), name="earnings"),
    path("<uuid:pk>/cash/", views.CashPayment.as_view(), name="cash"),
    path("<uuid:pk>/checkout/", views.Checkout.as_view(), name="checkout"),
]
