from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("materials.urls")),
    # DRF's own login/logout pages for trying the API in a browser.
    path("api-auth/", include("rest_framework.urls")),
]
