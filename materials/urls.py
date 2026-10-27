from django.urls import path

from . import views

urlpatterns = [
    path("areas/", views.area_list, name="area_list"),
    path("materials/", views.material_list, name="material_list"),
    path("impact/", views.impact, name="impact"),
    path("register/", views.register, name="register"),
    path("profile/", views.profile, name="profile"),
]
