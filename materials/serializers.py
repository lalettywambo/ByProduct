"""Turn models into JSON and check incoming JSON. The business rules themselves live in services.py.

Requests send ids ("material": 1). Responses show related objects with their id and name
("material": {"id": 1, "name": "Sawdust"}). So each resource has an *Input* serializer for requests
and a plain one for responses.
"""
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


# ---------- Small {"id", "name"} shapes used inside other responses ----------

class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "name", "latitude", "longitude"]


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = ["id", "name", "kg_per_sack"]


class AreaRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ["id", "name"]


class MaterialRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = ["id", "name"]


class BusinessRefSerializer(serializers.ModelSerializer):
    """Who a buyer or seller is, inside another response. Never a phone number."""
    area = AreaRefSerializer()

    class Meta:
        model = Business
        fields = ["id", "name", "area"]


# ---------- Businesses and accounts ----------

class PublicBusinessSerializer(serializers.ModelSerializer):
    """What anyone may see about a business. No phone: that is shared only after a pickup is agreed."""
    area = AreaRefSerializer()
    completed_pickups = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "completed_pickups"]

    def get_completed_pickups(self, business) -> int:
        return services.completed_pickups(business)


class BusinessSerializer(serializers.ModelSerializer):
    """Your own business, phone included. Only you see this (login, register, GET /api/me/)."""
    area = AreaRefSerializer()

    class Meta:
        model = Business
        fields = ["id", "name", "kind", "area", "phone"]


class BusinessInputSerializer(serializers.ModelSerializer):
    """PATCH /api/me/: the fields you can change on your business."""
    class Meta:
        model = Business
        fields = ["name", "kind", "area", "phone"]


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


# ---------- Listings ----------

class ListingSerializer(serializers.ModelSerializer):
    seller = BusinessRefSerializer(source="business")
    material = MaterialRefSerializer()
    kg_left = serializers.SerializerMethodField()
    is_live = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "seller", "material", "quantity", "unit", "kg", "kg_left", "condition", "price_per_unit",
                  "available_until", "description", "is_live", "created_at"]

    def get_kg_left(self, listing) -> int:
        return services.kg_left(listing)

    def get_is_live(self, listing) -> bool:
        return services.is_live(listing)


class ListingInputSerializer(serializers.ModelSerializer):
    """POST and PATCH listings/. kg is worked out from quantity and unit, so it isn't sent."""
    class Meta:
        model = Listing
        fields = ["material", "quantity", "unit", "condition", "price_per_unit", "available_until", "description"]

    def validate_available_until(self, day):
        return check_not_in_past(day)


# ---------- Pickups ----------

class PickupListingSerializer(serializers.ModelSerializer):
    """The listing a pickup is for, with how many kilos are still free on it."""
    material = MaterialRefSerializer()
    kg_left = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "material", "kg_left"]

    def get_kg_left(self, listing) -> int:
        return services.kg_left(listing)


class PickupSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display")
    listing = PickupListingSerializer()
    buyer = BusinessRefSerializer()
    seller = BusinessRefSerializer()
    other_phone = serializers.SerializerMethodField()

    class Meta:
        model = Pickup
        fields = ["id", "status", "status_label", "kg", "listing", "buyer", "seller", "other_phone",
                  "created_at", "updated_at"]

    def get_other_phone(self, pickup) -> str | None:
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
    """POST pickups/. The amount rules (at least 1 kg, not more than is left) stay in services."""
    need = serializers.PrimaryKeyRelatedField(queryset=Need.objects.all())
    listing = serializers.PrimaryKeyRelatedField(queryset=Listing.objects.all())
    kg = serializers.IntegerField()


class PickupKgSerializer(serializers.Serializer):
    """PATCH pickups/{id}/: the only thing a buyer may change on a request."""
    kg = serializers.IntegerField()


# ---------- Needs ----------

class ProgressSerializer(serializers.Serializer):
    collected = serializers.IntegerField()
    agreed = serializers.IntegerField()
    waiting = serializers.IntegerField()
    still_needed = serializers.IntegerField()


class NeedSerializer(serializers.ModelSerializer):
    business = BusinessRefSerializer()
    material = MaterialRefSerializer()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Need
        fields = ["id", "business", "material", "kg", "max_km", "needed_by", "note", "closed", "progress",
                  "created_at"]

    def get_progress(self, need):
        return services.progress(need)


class NeedDetailSerializer(NeedSerializer):
    """GET needs/{id}/: the need plus its pickup requests."""
    pickups = serializers.SerializerMethodField()

    class Meta(NeedSerializer.Meta):
        fields = NeedSerializer.Meta.fields + ["pickups"]

    def get_pickups(self, need):
        pickups = need.pickups.select_related("listing__material", "need__business__area",
                                              "listing__business__area")
        # The buyer is looking, so agreed pickups show the seller's phone.
        return PickupSerializer(pickups, many=True, context={"business": need.business}).data


class NeedInputSerializer(serializers.ModelSerializer):
    """POST and PATCH needs/."""
    class Meta:
        model = Need
        fields = ["material", "kg", "max_km", "needed_by", "note"]

    def validate_needed_by(self, day):
        return check_not_in_past(day)


class SuggestionListingSerializer(serializers.ModelSerializer):
    material = MaterialRefSerializer()

    class Meta:
        model = Listing
        fields = ["id", "material"]


class SuggestionSerializer(serializers.Serializer):
    """One row of plan(): a seller to ask and how much to ask them for. No phone."""
    listing = SuggestionListingSerializer()
    seller = BusinessRefSerializer(source="listing.business")
    km = serializers.FloatField()
    kg_left = serializers.IntegerField()
    suggested_kg = serializers.IntegerField()
