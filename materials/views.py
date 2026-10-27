"""API endpoints. Each view reads the request, calls a rule in services.py and returns JSON."""
from datetime import timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from . import services
from .models import Area, Business, Listing, Material, Need, Pickup
from .serializers import (AreaSerializer, AskSerializer, BusinessSerializer, DistanceSerializer,
                          ListingSerializer, MaterialSerializer, NeedSerializer, PickupSerializer,
                          RegisterSerializer, SuggestionSerializer)


def business_of(request):
    """The logged-in user's business. The admin superuser has none, so it can't buy or sell."""
    business = Business.objects.filter(user=request.user).first()
    if business is None:
        raise PermissionDenied("Add your business details first.")
    return business


def rule_error(error):
    """A rule in services.py said no: send its message back as a 400."""
    return Response({"detail": str(error)}, status=400)


# ---------- Public ----------

@api_view(["GET"])
@permission_classes([AllowAny])
def area_list(request):
    return Response(AreaSerializer(Area.objects.all(), many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def material_list(request):
    return Response(MaterialSerializer(Material.objects.all(), many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def impact(request):
    return Response(services.impact())



# ---------- Accounts ----------

@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    """Create the login and its business in one step."""
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    business = serializer.save()
    return Response({"username": business.user.username, "business": BusinessSerializer(business).data},
                    status=201)


@api_view(["GET", "PATCH"])
def profile(request):
    business = Business.objects.filter(user=request.user).first()
    if request.method == "GET":
        if business is None:
            raise NotFound("Add your business details first.")
        return Response(BusinessSerializer(business).data)
    # A user without a business yet (like the admin) creates one here; everyone else updates theirs.
    serializer = BusinessSerializer(business, data=request.data, partial=business is not None)
    serializer.is_valid(raise_exception=True)
    serializer.save(user=request.user)
    return Response(serializer.data)


# ---------- Listings (sellers) ----------

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def listing_list(request):
    """GET: anyone can browse live listings. POST: a logged-in business lists its leftovers."""
    if request.method == "POST":
        business = business_of(request)
        serializer = ListingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(business=business)
        return Response(serializer.data, status=201)

    today = timezone.localdate()
    listings = (Listing.objects.filter(withdrawn=False, available_until__gte=today)
                .select_related("business__area", "material"))
    material = request.query_params.get("material", "")
    area = request.query_params.get("area", "")
    # Ignore filter values that aren't ids instead of failing the whole request.
    if material.isdigit():
        listings = listings.filter(material_id=material)
    if area.isdigit():
        listings = listings.filter(business__area_id=area)
    # A fully promised listing isn't live, but kg_left is calculated, so that check happens in Python.
    live = [listing for listing in listings if services.kg_left(listing) > 0]
    return Response(ListingSerializer(live, many=True).data)


@api_view(["GET"])
def my_listings(request):
    """All of the seller's own listings, including expired and withdrawn ones."""
    listings = business_of(request).listings.select_related("business__area", "material")
    return Response(ListingSerializer(listings, many=True).data)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticatedOrReadOnly])
def listing_detail(request, pk):
    if request.method == "GET":
        return Response(ListingSerializer(get_object_or_404(Listing, pk=pk)).data)
    # Only the owner can edit; for anyone else the listing "isn't found".
    listing = get_object_or_404(Listing, pk=pk, business=business_of(request))
    serializer = ListingSerializer(listing, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(serializer.data)


@api_view(["POST"])
def listing_withdraw(request, pk):
    business = business_of(request)
    listing = get_object_or_404(Listing, pk=pk, business=business)
    try:
        declined = services.withdraw(listing, business)
    except services.RuleError as error:
        return rule_error(error)
    return Response({"message": f"Listing withdrawn. {declined} waiting request(s) were declined.",
                     "listing": ListingSerializer(listing).data})


@api_view(["POST"])
def listing_again(request, pk):
    business = business_of(request)
    listing = get_object_or_404(Listing, pk=pk, business=business)
    until = timezone.localdate() + timedelta(days=7)
    try:
        new_listing = services.list_again(listing, business, until)
    except services.RuleError as error:
        return rule_error(error)
    return Response({"message": f"Listed again until {until:%d %b}. Edit it to change the date or amount.",
                     "listing": ListingSerializer(new_listing).data}, status=201)


# ---------- Needs (buyers) ----------

def own_need(request, pk):
    """Only the business that posted a need can see or change it; for anyone else it doesn't exist."""
    business = business_of(request)
    return business, get_object_or_404(Need.objects.select_related("material", "business__area"),
                                       pk=pk, business=business)


@api_view(["GET", "POST"])
def need_list(request):
    business = business_of(request)
    if request.method == "POST":
        serializer = NeedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(business=business)
        return Response(serializer.data, status=201)
    return Response(NeedSerializer(business.needs.select_related("material"), many=True).data)


@api_view(["GET"])
def need_detail(request, pk):
    business, need = own_need(request, pk)
    pickups = need.pickups.select_related("listing__business", "listing__material", "need__business")
    data = NeedSerializer(need).data
    data["pickups"] = PickupSerializer(pickups, many=True, context={"business": business}).data
    return Response(data)


@api_view(["GET"])
def need_matches(request, pk):
    """The suggested sellers and amounts for this need, nearest first."""
    business, need = own_need(request, pk)
    if need.closed:
        return Response({"suggestions": [], "covered_kg": 0, "still_needed": 0})
    result = services.plan(need)
    return Response({
        "suggestions": SuggestionSerializer(result["suggestions"], many=True).data,
        "covered_kg": result["covered_kg"],
        "still_needed": services.kg_still_needed(need),
    })


@api_view(["POST"])
def need_ask(request, pk):
    business, need = own_need(request, pk)
    serializer = AskSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    listing = serializer.validated_data["listing"]
    try:
        pickup = services.ask_for_pickup(need, listing, serializer.validated_data["kg"], business)
    except services.RuleError as error:
        return rule_error(error)
    return Response({"message": f"Asked {listing.business} for {pickup.kg} kg.",
                     "pickup": PickupSerializer(pickup, context={"business": business}).data}, status=201)


@api_view(["POST"])
def need_ask_all(request, pk):
    business, need = own_need(request, pk)
    try:
        pickups = services.ask_all(need, business)
    except services.RuleError as error:
        return rule_error(error)
    total = sum(pickup.kg for pickup in pickups)
    return Response({"message": f"Asked the suggested sellers for {total} kg in total.",
                     "pickups": PickupSerializer(pickups, many=True, context={"business": business}).data},
                    status=201)


@api_view(["POST"])
def need_distance(request, pk):
    business, need = own_need(request, pk)
    serializer = DistanceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    need.max_km = serializer.validated_data["max_km"]
    need.save()
    return Response(NeedSerializer(need).data)


@api_view(["POST"])
def need_close(request, pk):
    business, need = own_need(request, pk)
    try:
        cancelled = services.close_need(need, business)
    except services.RuleError as error:
        return rule_error(error)
    return Response({"message": f"Need closed. {cancelled} unanswered request(s) were cancelled.",
                     "need": NeedSerializer(need).data})


# ---------- Pickups (both sides) ----------

def my_pickups(business):
    """Pickups where this business is the buyer or the seller."""
    return (Pickup.objects.filter(Q(need__business=business) | Q(listing__business=business))
            .select_related("need__business", "listing__business", "listing__material"))


@api_view(["GET"])
def pickup_list(request):
    """Optional ?status=pending (or accepted, collected...) to see e.g. requests waiting for an answer."""
    business = business_of(request)
    pickups = my_pickups(business)
    status = request.query_params.get("status")
    if status:
        pickups = pickups.filter(status=status)
    return Response(PickupSerializer(pickups, many=True, context={"business": business}).data)


@api_view(["POST"])
def pickup_action(request, pk, action):
    """accept / decline (seller), cancel (either side), collected (buyer). services.py decides who may."""
    business = business_of(request)
    pickup = get_object_or_404(my_pickups(business), pk=pk)
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
        elif action == "collected":
            services.mark_collected(pickup, business)
            message = f"Marked as collected: {pickup.kg} kg reused."
        else:
            raise NotFound("Unknown action.")
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
