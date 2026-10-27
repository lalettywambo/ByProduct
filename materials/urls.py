from django.urls import path

from . import views

urlpatterns = [
    path("basics/", views.basics, name="basics"),
    path("businesses/", views.business_create, name="business_create"),
    path("businesses/<int:pk>/", views.business_dashboard, name="business_dashboard"),
    path("listings/", views.listings, name="listings"),
    path("listings/<int:pk>/<str:action>/", views.listing_action, name="listing_action"),
    path("needs/", views.need_create, name="need_create"),
    path("needs/<int:pk>/", views.need_detail, name="need_detail"),
    path("needs/<int:pk>/<str:action>/", views.need_action, name="need_action"),
    path("pickups/<int:pk>/<str:action>/", views.pickup_action, name="pickup_action"),
    path("impact/", views.impact, name="impact"),
]
