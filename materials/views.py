"""API endpoints. Each view reads the request, calls a rule in services.py and returns JSON."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .models import Area, Business, Material
from .serializers import AreaSerializer, BusinessSerializer, MaterialSerializer, RegisterSerializer


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
