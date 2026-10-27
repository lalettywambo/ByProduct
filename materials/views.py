"""API endpoints. Each view finds the acting business, calls one rule in services.py and returns JSON."""
from datetime import timedelta

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import services
from .models import Area, Business, Listing, Material, Need, Pickup
from .serializers import (AreaSerializer, BusinessSerializer, ListingSerializer, MaterialSerializer,
                          NeedSerializer, PickupSerializer, PublicBusinessSerializer, SuggestionSerializer)


def acting_business(request):
    """No login in this version: each request says which business is acting."""
    business_id = request.data.get("business") or request.query_params.get("business")
    if not str(business_id).isdigit():
        raise ValidationError({"business": "Say which business is acting."})
    return get_object_or_404(Business, pk=business_id)


def rule_error(error):
    """A rule in services.py said no: send its message back as a 400."""
    return Response({"detail": str(error)}, status=400)


# ---------- Basics and businesses ----------

@api_view(["GET"])
def basics(request):
    """Everything a front end needs for its dropdowns and the "I am" business picker."""
    return Response({
        "areas": AreaSerializer(Area.objects.all(), many=True).data,
        "materials": MaterialSerializer(Material.objects.all(), many=True).data,
        "businesses": PublicBusinessSerializer(Business.objects.select_related("area"), many=True).data,
    })


@api_view(["POST"])
def business_create(request):
    serializer = BusinessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data, status=201)


@api_view(["GET"])
def business_dashboard(request, pk):
    """One business's view: its listings, open needs, requests waiting for its answer and agreed pickups."""
    business = get_object_or_404(Business.objects.select_related("area"), pk=pk)
    context = {"business": business}
    involved = Q(listing__business=business) | Q(need__business=business)
    pickups = Pickup.objects.select_related("need__business", "listing__business", "listing__material")
    waiting = pickups.filter(listing__business=business, status=Pickup.PENDING)
    agreed = pickups.filter(involved, status=Pickup.ACCEPTED)
    return Response({
        "business": PublicBusinessSerializer(business).data,
        "listings": ListingSerializer(business.listings.select_related("material", "business__area"),
                                      many=True).data,
        "open_needs": NeedSerializer(business.needs.filter(closed=False), many=True).data,
        "waiting_for_answer": PickupSerializer(waiting, many=True, context=context).data,
        "agreed_pickups": PickupSerializer(agreed, many=True, context=context).data,
    })


# ---------- Listings (sellers) ----------

@api_view(["GET", "POST"])
def listings(request):
    """GET: browse live listings, optionally ?material=<id>&area=<id>. POST: list leftovers."""
    if request.method == "POST":
        business = acting_business(request)
        serializer = ListingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(business=business)
        return Response(serializer.data, status=201)

    today = timezone.localdate()
    found = (Listing.objects.filter(withdrawn=False, available_until__gte=today)
             .select_related("business__area", "material"))
    material = request.query_params.get("material", "")
    area = request.query_params.get("area", "")
    # Ignore filter values that aren't ids instead of failing the whole request.
    if material.isdigit():
        found = found.filter(material_id=material)
    if area.isdigit():
        found = found.filter(business__area_id=area)
    # A fully promised listing isn't live, but kg_left is calculated, so that check happens in Python.
    live = [listing for listing in found if services.kg_left(listing) > 0]
    return Response(ListingSerializer(live, many=True).data)


@api_view(["POST"])
def listing_action(request, pk, action):
    """withdraw | again. services.py checks that the acting business is the seller."""
    if action not in ("withdraw", "again"):
        raise Http404("Unknown action.")
    listing = get_object_or_404(Listing, pk=pk)
    business = acting_business(request)
    try:
        if action == "withdraw":
            declined = services.withdraw(listing, business)
            return Response({"message": f"Listing withdrawn. {declined} waiting request(s) were declined.",
                             "listing": ListingSerializer(listing).data})
        until = timezone.localdate() + timedelta(days=7)
        new_listing = services.list_again(listing, business, until)
        return Response({"message": f"Listed again until {until:%d %b}.",
                         "listing": ListingSerializer(new_listing).data}, status=201)
    except services.RuleError as error:
        return rule_error(error)


# ---------- Needs (buyers) ----------

@api_view(["POST"])
def need_create(request):
    business = acting_business(request)
    serializer = NeedSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(business=business)
    return Response(serializer.data, status=201)


@api_view(["GET"])
def need_detail(request, pk):
    """Progress, suggested sellers and pickup requests in one response, for the buyer only."""
    business = acting_business(request)
    # Anyone but the buyer who posted the need gets "not found".
    need = get_object_or_404(Need.objects.select_related("material", "business__area"), pk=pk, business=business)
    result = {"suggestions": [], "covered_kg": 0} if need.closed else services.plan(need)
    pickups = need.pickups.select_related("listing__business", "listing__material", "need__business")
    data = NeedSerializer(need).data
    data["suggestions"] = SuggestionSerializer(result["suggestions"], many=True).data
    data["covered_kg"] = result["covered_kg"]
    data["pickups"] = PickupSerializer(pickups, many=True, context={"business": business}).data
    return Response(data)


@api_view(["POST"])
def need_action(request, pk, action):
    """ask | ask-all | distance | close. services.py checks that the acting business is the buyer."""
    if action not in ("ask", "ask-all", "distance", "close"):
        raise Http404("Unknown action.")
    need = get_object_or_404(Need, pk=pk)
    business = acting_business(request)
    try:
        if action == "ask":
            listing_id = request.data.get("listing", "")
            kg = request.data.get("kg", "")
            if not str(listing_id).isdigit():
                raise ValidationError({"listing": "Say which listing to ask."})
            if not str(kg).isdigit():
                raise ValidationError({"kg": "Enter the kilos as a whole number."})
            listing = get_object_or_404(Listing, pk=listing_id)
            pickup = services.ask_for_pickup(need, listing, int(kg), business)
            return Response({"message": f"Asked {listing.business} for {pickup.kg} kg.",
                             "pickup": PickupSerializer(pickup, context={"business": business}).data}, status=201)
        if action == "ask-all":
            pickups = services.ask_all(need, business)
            total = sum(pickup.kg for pickup in pickups)
            return Response({"message": f"Asked the suggested sellers for {total} kg in total.",
                             "pickups": PickupSerializer(pickups, many=True, context={"business": business}).data},
                            status=201)
        if action == "distance":
            services.change_distance(need, request.data.get("max_km", ""), business)
            return Response({"message": f"Showing sellers within {need.max_km} km.",
                             "need": NeedSerializer(need).data})
        cancelled = services.close_need(need, business)
        return Response({"message": f"Need closed. {cancelled} unanswered request(s) were cancelled.",
                         "need": NeedSerializer(need).data})
    except services.RuleError as error:
        return rule_error(error)


# ---------- Pickups (both sides) ----------

@api_view(["POST"])
def pickup_action(request, pk, action):
    """accept / decline (seller), cancel (either side), collected (buyer). services.py decides who may."""
    if action not in ("accept", "decline", "cancel", "collected"):
        raise Http404("Unknown action.")
    pickup = get_object_or_404(Pickup.objects.select_related("need__business", "listing__business"), pk=pk)
    business = acting_business(request)
    try:
        if action == "accept":
            pickup, declined = services.accept(pickup, business)
            message = f"Agreed. Call {pickup.buyer} on {pickup.buyer.phone} to arrange the pickup."
            if declined:
                message += f" {declined} other request(s) no longer fit and were declined."
        elif action == "decline":
            services.decline(pickup, business)
            message = "Declined."
        elif action == "cancel":
            services.cancel(pickup, business)
            message = "Pickup cancelled. Those kilos are free again."
        else:
            services.mark_collected(pickup, business)
            message = f"Marked as collected: {pickup.kg} kg reused."
    except services.RuleError as error:
        return rule_error(error)

    data = {
        "message": message,
        "pickup": PickupSerializer(pickup, context={"business": business}).data,
        "listing_kg_left": services.kg_left(pickup.listing),
    }
    # The need's progress is private to the buyer.
    if business == pickup.buyer:
        data["need_progress"] = services.progress(pickup.need)
    return Response(data)


# ---------- Impact ----------

@api_view(["GET"])
def impact(request):
    return Response(services.impact())
