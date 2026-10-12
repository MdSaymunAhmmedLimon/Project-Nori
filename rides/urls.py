from django.urls import path

from . import views

app_name = "rides"
urlpatterns = [
    path("places/search/", views.PlaceSearchView.as_view(), name="place-search"),
    path("quotes/", views.QuoteView.as_view(), name="quote"),
    path("", views.RideList.as_view(), name="list"),
    path("current/", views.CurrentRide.as_view(), name="current"),
    path("stats/", views.RiderStats.as_view(), name="stats"),
    path("schedule/", views.ScheduledRideList.as_view(), name="schedule-list"),
    path("schedule/<uuid:pk>/", views.ScheduledRideDetail.as_view(), name="schedule-detail"),
    path("promos/validate/", views.ValidatePromo.as_view(), name="promo-validate"),
    path("carpools/", views.CarpoolList.as_view(), name="carpool-list"),
    path("offers/", views.Offers.as_view(), name="offers"),
    path("offers/<int:pk>/accept/", views.OfferAction.as_view(), {"action": "accept"}, name="accept"),
    path("offers/<int:pk>/reject/", views.OfferAction.as_view(), {"action": "reject"}, name="reject"),
    path("sos/<int:pk>/resolve/", views.SOSResolve.as_view(), name="sos-resolve"),
    path("carpools/<uuid:pk>/join/", views.JoinCarpool.as_view(), name="join-carpool"),
    path("carpools/<uuid:pk>/leave/", views.LeaveCarpool.as_view(), name="leave-carpool"),
    path("saved-places/", views.SavedPlacesView.as_view(), name="saved-places"),
    path("saved-places/<int:pk>/", views.SavedPlaceDetailView.as_view(), name="saved-place-detail"),
    path("<uuid:pk>/", views.RideDetail.as_view(), name="detail"),
    path("<uuid:pk>/actions/", views.RideAction.as_view(), name="action"),
    path("<uuid:pk>/rating/", views.RateRide.as_view(), name="rating"),
    path("<uuid:pk>/tracking/", views.Tracking.as_view(), name="tracking"),
    path("<uuid:pk>/sos/", views.SOSView.as_view(), name="sos"),
    path("<uuid:pk>/carpool/", views.EnableCarpool.as_view(), name="enable-carpool"),
    path("<uuid:pk>/tip/", views.RideTipView.as_view(), name="tip"),
    path("<uuid:pk>/messages/", views.RideMessagesView.as_view(), name="messages"),
    path("<uuid:pk>/eta/", views.LiveETAView.as_view(), name="eta"),
]
