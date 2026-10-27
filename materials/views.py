"""API endpoints. Each view reads the request, calls a rule in services.py and returns JSON."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .models import Area, Business, Material
from .serializers import AreaSerializer, MaterialSerializer


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

