from django.urls import path

from . import views

app_name = "payments"
urlpatterns = [
    path("wallet/", views.WalletOverview.as_view(), name="wallet"),
    path("wallet/transactions/", views.WalletHistory.as_view(), name="wallet-history"),
    path("wallet/top-up/", views.WalletTopUp.as_view(), name="wallet-top-up"),
    path("webhook/stripe/", views.StripeWebhook.as_view(), name="stripe-webhook"),
    path("earnings/", views.Earnings.as_view(), name="earnings"),
    path("<uuid:pk>/cash/", views.CashPayment.as_view(), name="cash"),
    path("<uuid:pk>/checkout/", views.Checkout.as_view(), name="checkout"),
]
