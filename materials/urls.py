from django.urls import path

from . import views

urlpatterns = [
    path("areas/", views.area_list, name="area_list"),
    path("materials/", views.material_list, name="material_list"),
    path("impact/", views.impact, name="impact"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
    path("listings/", views.listing_list, name="listing_list"),
    path("listings/mine/", views.my_listings, name="my_listings"),
    path("listings/<int:pk>/", views.listing_detail, name="listing_detail"),
    path("listings/<int:pk>/withdraw/", views.listing_withdraw, name="listing_withdraw"),
    path("listings/<int:pk>/again/", views.listing_again, name="listing_again"),
    path("needs/", views.need_list, name="need_list"),
    path("needs/<int:pk>/", views.need_detail, name="need_detail"),
    path("needs/<int:pk>/matches/", views.need_matches, name="need_matches"),
    path("needs/<int:pk>/ask/", views.need_ask, name="need_ask"),
    path("needs/<int:pk>/ask-all/", views.need_ask_all, name="need_ask_all"),
    path("needs/<int:pk>/distance/", views.need_distance, name="need_distance"),
    path("needs/<int:pk>/close/", views.need_close, name="need_close"),
]
