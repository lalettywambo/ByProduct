from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from . import views
from .serializers import LoginSerializer

# The router writes the URLs for each ViewSet, e.g. listings/, listings/{id}/ and listings/{id}/withdraw/.
router = DefaultRouter()
router.register("areas", views.AreaViewSet, basename="area")
router.register("materials", views.MaterialViewSet, basename="material")
router.register("businesses", views.BusinessViewSet, basename="business")
router.register("listings", views.ListingViewSet, basename="listing")
router.register("needs", views.NeedViewSet, basename="need")
router.register("pickups", views.PickupViewSet, basename="pickup")

urlpatterns = [
    # Logging in: simplejwt's own views, with our login serializer so the response includes the business.
    path("auth/register/", views.register, name="register"),
    path("auth/login/", TokenObtainPairView.as_view(serializer_class=LoginSerializer), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="logout"),
    path("me/", views.me, name="me"),
    path("impact/", views.impact, name="impact"),
    path("", include(router.urls)),
]
