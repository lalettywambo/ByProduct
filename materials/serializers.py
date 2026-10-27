"""Turn models into JSON and check incoming JSON. The business rules themselves live in services.py."""
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
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


class PublicBusinessSerializer(serializers.ModelSerializer):
    """What anyone may see about a business. No phone: that is shared only after a pickup is agreed."""
    area = serializers.StringRelatedField()
    completed_pickups = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "completed_pickups"]

    def get_completed_pickups(self, business):
        return services.completed_pickups(business)


class BusinessSerializer(serializers.ModelSerializer):
    """The logged-in business's own profile, phone included."""
    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "phone"]


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)
    business = BusinessSerializer()

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate_password(self, value):
        validate_password(value)  # Django's built-in strength checks
        return value

    def create(self, data):
        # create_user hashes the password; the raw password is never stored.
        user = User.objects.create_user(username=data["username"], password=data["password"])
        return Business.objects.create(user=user, **data["business"])


class ListingSerializer(serializers.ModelSerializer):
    business = PublicBusinessSerializer(read_only=True)
    material_name = serializers.StringRelatedField(source="material", read_only=True)
    kg_left = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "business", "material", "material_name", "quantity", "unit", "kg", "kg_left",
                  "condition", "price_per_unit", "available_until", "description", "withdrawn", "created_at"]
        read_only_fields = ["kg", "withdrawn", "created_at"]

    def get_kg_left(self, listing):
        return services.kg_left(listing)

    def validate_available_until(self, day):
        return check_not_in_past(day)

    def validate(self, data):
        listing = self.instance
        if listing is None:
            return data
        if "material" in data and data["material"] != listing.material:
            raise serializers.ValidationError("The material can't change once a listing exists.")
        # A seller can shrink a listing, but never below what buyers were already promised.
        quantity = data.get("quantity", listing.quantity)
        unit = data.get("unit", listing.unit)
        new_kg = quantity * (listing.material.kg_per_sack if unit == "sack" else 1)
        promised = services.kg_promised(listing)
        if new_kg < promised:
            raise serializers.ValidationError(
                f"{promised} kg is already promised to buyers, so the listing can't be smaller than that.")
        return data


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
    """One row of plan(): a seller to ask and how much to ask them for."""
    listing = ListingSerializer()
    km = serializers.FloatField()
    kg_left = serializers.IntegerField()
    suggested_kg = serializers.IntegerField()
