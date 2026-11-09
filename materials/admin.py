from django.contrib import admin

from .models import Area, Business, Listing, Material, Need, Pickup


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ["name", "latitude", "longitude"]
    search_fields = ["name"]


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["name", "kg_per_sack"]


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "area", "phone", "user"]
    list_filter = ["area"]
    search_fields = ["name"]


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ["business", "material", "quantity", "unit", "kg", "available_until", "withdrawn"]
    list_filter = ["material", "withdrawn"]


@admin.register(Need)
class NeedAdmin(admin.ModelAdmin):
    list_display = ["business", "material", "kg", "max_km", "needed_by", "closed"]
    list_filter = ["material", "closed"]


@admin.register(Pickup)
class PickupAdmin(admin.ModelAdmin):
    list_display = ["need", "listing", "kg", "status", "updated_at"]
    list_filter = ["status"]
