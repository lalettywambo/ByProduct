"""Turn models into JSON and check incoming JSON. The business rules themselves live in services.py."""
from django.utils import timezone
from rest_framework import serializers

from . import services
from .models import Area, Business, Listing, Material, Need, Pickup


def check_not_in_past(day):
    if day < timezone.localdate():
        raise serializers.ValidationError("Pick today or a later date.")
    return day


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "name", "latitude", "longitude"]


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = ["id", "name", "kg_per_sack"]


class BusinessSerializer(serializers.ModelSerializer):
    """Used when adding a business, so the phone is accepted here."""
    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "phone"]


class PublicBusinessSerializer(serializers.ModelSerializer):
    """What anyone may see about a business. No phone: that is shared only after a pickup is agreed."""
    area = serializers.StringRelatedField()
    completed_pickups = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "completed_pickups"]

    def get_completed_pickups(self, business):
        return services.completed_pickups(business)


class ListingSerializer(serializers.ModelSerializer):
    business = PublicBusinessSerializer(read_only=True)
    material_name = serializers.StringRelatedField(source="material", read_only=True)
    kg_left = serializers.SerializerMethodField()
    live = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "business", "material", "material_name", "quantity", "unit", "kg", "kg_left", "live",
                  "condition", "price_per_unit", "available_until", "description", "withdrawn", "created_at"]
        read_only_fields = ["kg", "withdrawn", "created_at"]

    def get_kg_left(self, listing):
        return services.kg_left(listing)

    def get_live(self, listing):
        return services.is_live(listing)

    def validate_available_until(self, day):
        return check_not_in_past(day)


class NeedSerializer(serializers.ModelSerializer):
    material_name = serializers.StringRelatedField(source="material", read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Need
        fields = ["id", "material", "material_name", "kg", "max_km", "needed_by", "note", "closed",
                  "created_at", "progress"]
        read_only_fields = ["closed", "created_at"]

    def get_progress(self, need):
        return services.progress(need)

    def validate_needed_by(self, day):
        return check_not_in_past(day)


class PickupSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    material = serializers.StringRelatedField(source="listing.material")
    buyer = serializers.StringRelatedField()
    seller = serializers.StringRelatedField()
    other_phone = serializers.SerializerMethodField()

    class Meta:
        model = Pickup
        fields = ["id", "need", "listing", "material", "kg", "status", "status_label", "buyer", "seller",
                  "other_phone", "created_at", "updated_at"]

    def get_other_phone(self, pickup):
        """Each side sees the other's phone only once the seller has agreed."""
        if pickup.status not in services.HOLDS_KILOS:
            return None
        viewer = self.context.get("business")
        if viewer == pickup.buyer:
            return pickup.seller.phone
        if viewer == pickup.seller:
            return pickup.buyer.phone
        return None


class SuggestionSerializer(serializers.Serializer):
    """One row of plan(): a seller to ask and how much to ask them for. Flat, and no phone."""
    listing = serializers.IntegerField(source="listing.id")
    seller = serializers.CharField(source="listing.business.name")
    area = serializers.CharField(source="listing.business.area.name")
    km = serializers.FloatField()
    kg_left = serializers.IntegerField()
    suggested_kg = serializers.IntegerField()
