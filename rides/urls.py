from django.urls import path

from . import views

app_name = "rides"
urlpatterns = [
    path("quotes/", views.QuoteView.as_view(), name="quote"),
    path("", views.RideList.as_view(), name="list"),
    path("current/", views.CurrentRide.as_view(), name="current"),
    path("offers/", views.Offers.as_view(), name="offers"),
    path("offers/<int:pk>/accept/", views.OfferAction.as_view(), {"action": "accept"}, name="accept"),
    path("offers/<int:pk>/reject/", views.OfferAction.as_view(), {"action": "reject"}, name="reject"),
    path("<uuid:pk>/", views.RideDetail.as_view(), name="detail"),
    path("<uuid:pk>/actions/", views.RideAction.as_view(), name="action"),
    path("<uuid:pk>/rating/", views.RateRide.as_view(), name="rating"),
    path("<uuid:pk>/tracking/", views.Tracking.as_view(), name="tracking"),
]
