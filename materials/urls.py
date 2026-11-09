from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from . import views
from .serializers import LoginSerializer

urlpatterns = [
    # Logging in: simplejwt's own views, with our login serializer so the response includes the business.
    path("auth/register/", views.register, name="register"),
    path("auth/login/", TokenObtainPairView.as_view(serializer_class=LoginSerializer), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="logout"),
    path("me/", views.me, name="me"),

    path("basics/", views.basics, name="basics"),
    path("listings/", views.listings, name="listings"),
    path("listings/<int:pk>/<str:action>/", views.listing_action, name="listing_action"),
    path("needs/", views.need_create, name="need_create"),
    path("needs/<int:pk>/", views.need_detail, name="need_detail"),
    path("needs/<int:pk>/<str:action>/", views.need_action, name="need_action"),
    path("pickups/<int:pk>/<str:action>/", views.pickup_action, name="pickup_action"),
    path("impact/", views.impact, name="impact"),
]
