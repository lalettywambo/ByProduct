"""Turn models into JSON and check incoming JSON. The business rules themselves live in services.py."""
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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
    """Your own business, phone included. Other people only ever see PublicBusinessSerializer."""
    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "phone"]


class RegisterSerializer(serializers.ModelSerializer):
    """Sign-up in one flat form: the login (username, password) and the business details."""
    username = serializers.CharField(max_length=150, write_only=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = Business
        fields = ["username", "password", "name", "kind", "area", "phone"]

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("That username is taken.")
        return value

    def validate(self, data):
        # Django's built-in password checks: long enough, not too common, not too like the username.
        try:
            validate_password(data["password"], user=User(username=data["username"]))
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": list(error.messages)})
        return data

    @transaction.atomic
    def create(self, data):
        # create_user stores a hash of the password, never the password itself.
        user = User.objects.create_user(username=data.pop("username"), password=data.pop("password"))
        return Business.objects.create(user=user, **data)


class LoginSerializer(TokenObtainPairSerializer):
    """simplejwt's login (username + password in, access + refresh tokens out), plus the business."""

    def validate(self, attrs):
        data = super().validate(attrs)
        business = Business.objects.filter(user=self.user).first()
        data["business"] = BusinessSerializer(business).data if business else None
        return data


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


class PickupCreateSerializer(serializers.Serializer):
    """Input for asking a seller. The amount rules (at least 1 kg, not more than is left) stay in services."""
    need = serializers.PrimaryKeyRelatedField(queryset=Need.objects.all())
    listing = serializers.PrimaryKeyRelatedField(queryset=Listing.objects.all())
    kg = serializers.IntegerField()


class PickupKgSerializer(serializers.Serializer):
    """The only thing a buyer may PATCH on a pickup request."""
    kg = serializers.IntegerField()


class SuggestionSerializer(serializers.Serializer):
    """One row of plan(): a seller to ask and how much to ask them for. No phone."""
    listing = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    km = serializers.FloatField()
    kg_left = serializers.IntegerField()
    suggested_kg = serializers.IntegerField()

    def get_listing(self, row):
        listing = row["listing"]
        return {"id": listing.id, "material": {"id": listing.material_id, "name": listing.material.name}}

    def get_seller(self, row):
        seller = row["listing"].business
        return {"id": seller.id, "name": seller.name, "area": seller.area.name}
