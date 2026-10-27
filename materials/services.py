"""ByProduct rules. Views call these functions; they never change pickups directly."""
from math import asin, cos, radians, sin, sqrt

from django.db.models import Sum
from django.utils import timezone

from .models import Listing, Pickup

HOLDS_KILOS = [Pickup.ACCEPTED, Pickup.COLLECTED]                       # take kilos off a listing
COUNTS_FOR_NEED = [Pickup.PENDING, Pickup.ACCEPTED, Pickup.COLLECTED]   # already asked for
ACTIVE = [Pickup.PENDING, Pickup.ACCEPTED]


class RuleError(Exception):
    """Raised when someone tries something the rules don't allow. The message is shown to the user."""


def distance_km(a, b):
    """Haversine distance between two areas' centre points."""
    lat1, lng1, lat2, lng2 = map(radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def _sum(pickups):
    return pickups.aggregate(total=Sum("kg"))["total"] or 0


def kg_promised(listing):
    """Kilos already agreed or collected from this listing."""
    return _sum(listing.pickups.filter(status__in=HOLDS_KILOS))


def kg_left(listing):
    """Kilos nobody has been promised yet. Waiting requests don't hold kilos; agreed ones do."""
    return listing.kg - kg_promised(listing)


def kg_still_needed(need):
    """Kilos the buyer hasn't asked anyone for yet."""
    return max(0, need.kg - _sum(need.pickups.filter(status__in=COUNTS_FOR_NEED)))


def kg_collected(need):
    return _sum(need.pickups.filter(status=Pickup.COLLECTED))


def progress(need):
    """The four numbers behind a need's progress bar."""
    return {
        "collected": kg_collected(need),
        "agreed": _sum(need.pickups.filter(status=Pickup.ACCEPTED)),
        "waiting": _sum(need.pickups.filter(status=Pickup.PENDING)),
        "still_needed": kg_still_needed(need),
    }


def is_live(listing, today=None):
    today = today or timezone.localdate()
    return not listing.withdrawn and listing.available_until >= today and kg_left(listing) > 0


def find_matches(need, today=None):
    """Live listings of the same material within the buyer's distance, nearest first, then biggest.
    Each seller is suggested once per need: anyone already asked (even if they declined) is left out."""
    already = set(need.pickups.values_list("listing_id", flat=True))
    rows = []
    candidates = (Listing.objects.filter(material=need.material, withdrawn=False)
                  .exclude(business=need.business).select_related("business__area"))
    for listing in candidates:
        if listing.id in already or not is_live(listing, today):
            continue
        km = distance_km(listing.business.area, need.business.area)
        if km <= need.max_km:
            rows.append({"listing": listing, "km": round(km, 1), "kg_left": kg_left(listing)})
    rows.sort(key=lambda r: (r["km"], -r["kg_left"]))
    return rows


def plan(need, today=None):
    """Go down the matches and suggest how much to ask each seller for, until the need is covered."""
    still_needed = kg_still_needed(need)
    suggestions = []
    for row in find_matches(need, today):
        if still_needed <= 0:
            break
        take = min(row["kg_left"], still_needed)
        suggestions.append({**row, "suggested_kg": take})
        still_needed -= take
    return {"suggestions": suggestions, "covered_kg": sum(s["suggested_kg"] for s in suggestions)}
