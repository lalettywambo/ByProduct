"""API endpoints. Each view finds the logged-in business, calls one rule in services.py and returns JSON.

The ViewSets below each group every endpoint of one resource. The router in urls.py turns their methods
into URLs: list (GET /things/), create (POST /things/), retrieve (GET /things/{id}/),
partial_update (PATCH /things/{id}/), destroy (DELETE /things/{id}/), and each @action (an extra button
such as POST /things/{id}/withdraw/).

Response rule: CRUD endpoints return the resource itself. Action buttons return
{"message": "...", "<resource>": {...}} so the React app can show the message and update its state.
@extend_schema tells /api/docs/ the real request and response bodies.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from . import services
from .models import Area, Business, Listing, Material, Need, Pickup
from .serializers import (AreaSerializer, AskAllSerializer, BusinessInputSerializer, BusinessSerializer,
                          DashboardSerializer, ImpactSerializer, ListingActionSerializer, ListingInputSerializer,
                          ListingSerializer, LoginSerializer, MaterialSerializer, NeedActionSerializer,
                          NeedDetailSerializer, NeedInputSerializer, NeedSerializer, PickupActionSerializer,
                          PickupCreateSerializer, PickupKgSerializer, PickupSerializer, PublicBusinessSerializer,
                          RegisterSerializer, SuggestionSerializer, SuggestionsResponseSerializer,
                          TokensSerializer)

# Only these HTTP methods exist; there's no PUT, so every edit is a PATCH of the fields that change.
METHODS = ["get", "post", "patch", "delete"]


def my_business(request):
    """The logged-in user's business. A login without one (like the admin) can't buy or sell."""
    business = Business.objects.filter(user=request.user).first()
    if business is None:
        raise PermissionDenied("This account has no business profile.")
    return business


def api_exception_handler(error, context):
    """Turn a rule in services.py saying no into a response, so views don't need try/except everywhere.
    The wrong business acting is 403; anything else (state, amounts) is 400. Set in settings.py."""
    if isinstance(error, services.RuleError):
        status = 403 if isinstance(error, services.NotAllowed) else 400
        return Response({"detail": str(error)}, status=status)
    return drf_exception_handler(error, context)


# ---------- Accounts ----------

@extend_schema(request=RegisterSerializer, responses={201: TokensSerializer})
@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    """Sign up, and get tokens straight away so the React app is logged in after sign-up."""
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    business = serializer.save()
    refresh = RefreshToken.for_user(business.user)
    return Response({"access": str(refresh.access_token), "refresh": str(refresh),
                     "business": BusinessSerializer(business).data}, status=201)


@extend_schema(responses=TokensSerializer)
class LoginView(TokenObtainPairView):
    """simplejwt's login view, with our serializer so the response also includes the business."""
    serializer_class = LoginSerializer


@extend_schema(methods=["GET"], responses=DashboardSerializer)
@extend_schema(methods=["PATCH"], request=BusinessInputSerializer, responses=BusinessSerializer)
@extend_schema(methods=["DELETE"], responses={204: None})
@api_view(["GET", "PATCH", "DELETE"])
def me(request):
    """GET: my business and dashboard. PATCH: edit my business. DELETE: close my account."""
    business = my_business(request)
    if request.method == "PATCH":
        serializer = BusinessInputSerializer(business, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(BusinessSerializer(business).data)
    if request.method == "DELETE":
        services.delete_business(business, business)
        return Response(status=204)

    context = {"business": business}
    involved = Q(listing__business=business) | Q(need__business=business)
    pickups = Pickup.objects.select_related("need__business__area", "listing__business__area", "listing__material")
    waiting = pickups.filter(listing__business=business, status=Pickup.PENDING)
    agreed = pickups.filter(involved, status=Pickup.ACCEPTED)
    return Response({
        "business": BusinessSerializer(business).data,
        "completed_pickups": services.completed_pickups(business),
        "listings": ListingSerializer(business.listings.select_related("material", "business__area"),
                                      many=True).data,
        "open_needs": NeedSerializer(business.needs.filter(closed=False).select_related("material", "business__area"),
                                     many=True).data,
        "waiting_for_answer": PickupSerializer(waiting, many=True, context=context).data,
        "agreed_pickups": PickupSerializer(agreed, many=True, context=context).data,
    })


@extend_schema(responses=ImpactSerializer)
@api_view(["GET"])
@permission_classes([AllowAny])
def impact(request):
    return Response(services.impact())


# ---------- Reference lists (public, read-only) ----------

class AreaViewSet(viewsets.ReadOnlyModelViewSet):
    """GET areas/ and areas/{id}/. ReadOnlyModelViewSet gives only list and retrieve."""
    queryset = Area.objects.all()
    serializer_class = AreaSerializer
    permission_classes = [AllowAny]


class MaterialViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer
    permission_classes = [AllowAny]


class BusinessViewSet(viewsets.ReadOnlyModelViewSet):
    """Public profiles: name, kind, area and completed pickups. Never a phone number."""
    queryset = Business.objects.select_related("area").order_by("name")   # a fixed order keeps pages stable
    serializer_class = PublicBusinessSerializer
    permission_classes = [AllowAny]


# ---------- Listings (sellers) ----------

@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("material", int, description="Material id"),
                                   OpenApiParameter("area", int, description="Area id")]),
    create=extend_schema(request=ListingInputSerializer, responses={201: ListingSerializer}),
    partial_update=extend_schema(request=ListingInputSerializer, responses=ListingSerializer),
)
class ListingViewSet(viewsets.ModelViewSet):
    """Anyone can browse; a logged-in business lists leftovers; only the seller can change one."""
    serializer_class = ListingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    http_method_names = METHODS

    def get_queryset(self):
        """Which listings this request can see. Browsing shows live ones only; one listing by id can be any."""
        if self.action != "list":
            return Listing.objects.select_related("business__area", "material")
        listings = services.live_listings().select_related("business__area", "material")
        material = self.request.query_params.get("material", "")
        area = self.request.query_params.get("area", "")
        # Ignore filter values that aren't ids instead of failing the whole request.
        if material.isdigit():
            listings = listings.filter(material_id=material)
        if area.isdigit():
            listings = listings.filter(business__area_id=area)
        return listings

    def create(self, request, *args, **kwargs):
        """POST listings/: check the input, save it with the logged-in business as the seller."""
        serializer = ListingInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        listing = serializer.save(business=my_business(request))
        return Response(ListingSerializer(listing).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        """PATCH listings/{id}/: the serializer checks each field, services.edit_listing checks the rules."""
        listing = self.get_object()
        serializer = ListingInputSerializer(listing, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.edit_listing(listing, my_business(request), serializer.validated_data)
        return Response(ListingSerializer(listing).data)

    def destroy(self, request, *args, **kwargs):
        """DELETE listings/{id}/."""
        services.delete_listing(self.get_object(), my_business(request))
        return Response(status=204)

    @extend_schema(request=None, responses=ListingActionSerializer)
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        listing = self.get_object()
        declined = services.withdraw(listing, my_business(request))
        return Response({"message": f"Listing withdrawn. {declined} waiting request(s) were declined.",
                         "listing": ListingSerializer(listing).data})

    @extend_schema(request=None, responses={201: ListingActionSerializer})
    @action(detail=True, methods=["post"])
    def relist(self, request, pk=None):
        until = timezone.localdate() + timedelta(days=7)
        new_listing = services.list_again(self.get_object(), my_business(request), until)
        return Response({"message": f"Listed again until {until:%d %b}.",
                         "listing": ListingSerializer(new_listing).data}, status=201)


# ---------- Needs (buyers) ----------

@extend_schema_view(
    retrieve=extend_schema(responses=NeedDetailSerializer),
    create=extend_schema(request=NeedInputSerializer, responses={201: NeedSerializer}),
    partial_update=extend_schema(request=NeedInputSerializer, responses=NeedSerializer),
)
class NeedViewSet(viewsets.ModelViewSet):
    """A buyer's needs. Only the buyer who posted a need can see it; for anyone else it doesn't exist."""
    serializer_class = NeedSerializer
    http_method_names = METHODS

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):   # the docs page asks without a login
            return Need.objects.none()
        return Need.objects.filter(business=my_business(self.request)).select_related("material", "business__area")

    def create(self, request, *args, **kwargs):
        serializer = NeedInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        need = serializer.save(business=my_business(request))
        return Response(NeedSerializer(need).data, status=201)

    def retrieve(self, request, *args, **kwargs):
        """GET needs/{id}/: the need, its progress and its pickup requests."""
        return Response(NeedDetailSerializer(self.get_object()).data)

    def partial_update(self, request, *args, **kwargs):
        """PATCH needs/{id}/: kg, max_km, needed_by or note. Changing max_km is how the buyer widens the search."""
        need = self.get_object()
        serializer = NeedInputSerializer(need, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.edit_need(need, my_business(request), serializer.validated_data)
        return Response(NeedSerializer(need).data)

    def destroy(self, request, *args, **kwargs):
        services.delete_need(self.get_object(), my_business(request))
        return Response(status=204)

    @extend_schema(responses=SuggestionsResponseSerializer)
    @action(detail=True, methods=["get"])
    def suggestions(self, request, pk=None):
        """Which sellers to ask and how much, nearest first."""
        need = self.get_object()
        result = {"suggestions": [], "covered_kg": 0} if need.closed else services.plan(need)
        return Response({
            "need": NeedSerializer(need).data,
            "still_needed": services.kg_still_needed(need),
            "covered_kg": result["covered_kg"],
            "suggestions": SuggestionSerializer(result["suggestions"], many=True).data,
        })

    @extend_schema(request=None, responses={201: AskAllSerializer})
    @action(detail=True, methods=["post"], url_path="ask-all")
    def ask_all(self, request, pk=None):
        business = my_business(request)
        pickups = services.ask_all(self.get_object(), business)
        total = sum(pickup.kg for pickup in pickups)
        return Response({"message": f"Asked the suggested sellers for {total} kg in total.",
                         "pickups": PickupSerializer(pickups, many=True, context={"business": business}).data},
                        status=201)

    @extend_schema(request=None, responses=NeedActionSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        need = self.get_object()
        cancelled = services.close_need(need, my_business(request))
        return Response({"message": f"Need closed. {cancelled} unanswered request(s) were cancelled.",
                         "need": NeedSerializer(need).data})


# ---------- Pickups (both sides) ----------

@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter(
        "status", str, enum=[value for value, label in Pickup.STATUSES],
        description="Only pickups with this status, e.g. pending for requests waiting for an answer")]),
    create=extend_schema(request=PickupCreateSerializer, responses={201: PickupSerializer}),
    partial_update=extend_schema(request=PickupKgSerializer, responses=PickupSerializer),
)
class PickupViewSet(viewsets.ModelViewSet):
    """Pickup requests where the logged-in business is the buyer or the seller. Anyone else gets 404."""
    serializer_class = PickupSerializer
    http_method_names = METHODS

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):   # the docs page asks without a login
            return Pickup.objects.none()
        business = my_business(self.request)
        pickups = (Pickup.objects.filter(Q(need__business=business) | Q(listing__business=business))
                   .select_related("need__business__area", "listing__business__area", "listing__material"))
        status = self.request.query_params.get("status")
        if self.action == "list" and status:
            pickups = pickups.filter(status=status)
        return pickups

    def get_serializer_context(self):
        # Tells PickupSerializer who is looking, so it shows the *other* side's phone once agreed.
        context = super().get_serializer_context()
        user = self.request.user
        context["business"] = Business.objects.filter(user=user).first() if user.is_authenticated else None
        return context

    def create(self, request, *args, **kwargs):
        """POST pickups/ {need, listing, kg}: the buyer asks one seller."""
        serializer = PickupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pickup = services.ask_for_pickup(data["need"], data["listing"], data["kg"], my_business(request))
        return Response(self.get_serializer(pickup).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        """PATCH pickups/{id}/ {kg}: only the amount, only while the seller hasn't answered."""
        pickup = self.get_object()
        serializer = PickupKgSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_pickup_kg(pickup, my_business(request), serializer.validated_data["kg"])
        return Response(self.get_serializer(pickup).data)

    def destroy(self, request, *args, **kwargs):
        services.delete_pickup(self.get_object(), my_business(request))
        return Response(status=204)

    def answer(self, pickup, message):
        """What every pickup button returns: the message to show and the pickup as it is now."""
        return Response({"message": message, "pickup": self.get_serializer(pickup).data})

    @extend_schema(request=None, responses=PickupActionSerializer)
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        pickup, declined = services.accept(self.get_object(), my_business(request))
        message = f"Agreed. Call {pickup.buyer} on {pickup.buyer.phone} to arrange the pickup."
        if declined:
            message += f" {declined} other request(s) no longer fit and were declined."
        return self.answer(pickup, message)

    @extend_schema(request=None, responses=PickupActionSerializer)
    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        pickup = services.decline(self.get_object(), my_business(request))
        return self.answer(pickup, "Declined.")

    @extend_schema(request=None, responses=PickupActionSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        pickup = services.cancel(self.get_object(), my_business(request))
        return self.answer(pickup, "Pickup cancelled. Those kilos are free again.")

    @extend_schema(request=None, responses=PickupActionSerializer)
    @action(detail=True, methods=["post"])
    def collect(self, request, pk=None):
        pickup = services.mark_collected(self.get_object(), my_business(request))
        return self.answer(pickup, f"Marked as collected: {pickup.kg} kg reused.")
