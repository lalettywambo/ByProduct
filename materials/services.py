"""ByProduct rules. Views call these functions; they never change pickups directly."""
from math import asin, cos, radians, sin, sqrt

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Listing, Need, Pickup

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


@transaction.atomic
def ask_for_pickup(need, listing, kg, by, today=None):
    if by != need.business:
        raise RuleError("Only the business that posted this need can ask for pickups.")
    if need.closed:
        raise RuleError("This need is closed.")
    if listing.business == need.business:
        raise RuleError("You can't ask for your own listing.")
    if listing.material_id != need.material_id:
        raise RuleError("That listing is a different material.")
    if not is_live(listing, today):
        raise RuleError("That listing is no longer available.")
    if need.pickups.filter(listing=listing, status__in=ACTIVE).exists():
        raise RuleError("You've already asked this seller. Wait for their answer.")
    if kg <= 0:
        raise RuleError("Ask for at least 1 kg.")
    if kg > kg_left(listing):
        raise RuleError(f"Only {kg_left(listing)} kg is left on that listing.")
    if kg > kg_still_needed(need):
        raise RuleError(f"You only need {kg_still_needed(need)} kg more.")
    return Pickup.objects.create(listing=listing, need=need, kg=kg)


@transaction.atomic
def ask_all(need, by, today=None):
    """One click: ask every suggested seller for their suggested amount. All or nothing."""
    suggestions = plan(need, today)["suggestions"]
    return [ask_for_pickup(need, s["listing"], s["suggested_kg"], by, today) for s in suggestions]


@transaction.atomic
def accept(pickup, by):
    # Lock the rows so two buyers accepted at the same moment can't both get the last kilos.
    pickup = Pickup.objects.select_for_update().select_related("listing__business").get(pk=pickup.pk)
    listing = Listing.objects.select_for_update().get(pk=pickup.listing_id)
    if by != pickup.seller:
        raise RuleError("Only the seller can accept.")
    if pickup.status != Pickup.PENDING:
        raise RuleError("Only waiting requests can be accepted.")
    left = kg_left(listing)
    if pickup.kg > left:
        raise RuleError(f"Only {left} kg is left, so this request can't be accepted.")
    pickup.status = Pickup.ACCEPTED
    pickup.save()
    # Waiting requests that no longer fit are declined automatically.
    left = kg_left(listing)
    declined = listing.pickups.filter(status=Pickup.PENDING, kg__gt=left).update(status=Pickup.DECLINED)
    return pickup, declined


def decline(pickup, by):
    if by != pickup.seller:
        raise RuleError("Only the seller can decline.")
    if pickup.status != Pickup.PENDING:
        raise RuleError("Only waiting requests can be declined.")
    pickup.status = Pickup.DECLINED
    pickup.save()
    return pickup


def cancel(pickup, by):
    """The buyer can cancel a waiting request. Either side can cancel an agreed pickup (plans change, no-shows)."""
    buyer_cancels_waiting = pickup.status == Pickup.PENDING and by == pickup.buyer
    either_cancels_agreed = pickup.status == Pickup.ACCEPTED and by in (pickup.buyer, pickup.seller)
    if not (buyer_cancels_waiting or either_cancels_agreed):
        raise RuleError("You can't cancel this pickup.")
    # No kilos to hand back: kg_left is calculated, so a cancelled pickup simply stops counting.
    pickup.status = Pickup.CANCELLED
    pickup.save()
    return pickup


def mark_collected(pickup, by):
    """The buyer confirms they took the material. Only collected kilos count as reused."""
    if by != pickup.buyer:
        raise RuleError("Only the buyer can mark a pickup as collected.")
    if pickup.status != Pickup.ACCEPTED:
        raise RuleError("Only agreed pickups can be marked collected.")
    pickup.status = Pickup.COLLECTED
    pickup.save()
    need = pickup.need
    if kg_collected(need) >= need.kg:
        need.closed = True
        need.save()
    return pickup


def close_need(need, by):
    """The buyer stops looking. Requests still waiting for an answer are cancelled; agreed pickups stay."""
    if by != need.business:
        raise RuleError("Only the buyer can close this need.")
    need.closed = True
    need.save()
    return need.pickups.filter(status=Pickup.PENDING).update(status=Pickup.CANCELLED)


def change_distance(need, max_km, by):
    """The buyer widens or narrows the search, e.g. to 20 km when nobody nearby has enough."""
    if by != need.business:
        raise RuleError("Only the buyer can change this need.")
    # max_km may arrive as text from a form, so compare as text against the allowed choices.
    if str(max_km) not in [str(km) for km, label in Need.DISTANCES]:
        raise RuleError("Pick 5, 10 or 20 km.")
    need.max_km = int(max_km)
    need.save()
    return need


def withdraw(listing, by):
    """The seller takes a listing down. Waiting requests are declined; agreed pickups stay."""
    if by != listing.business:
        raise RuleError("Only the seller can withdraw this listing.")
    listing.withdrawn = True
    listing.save()
    return listing.pickups.filter(status=Pickup.PENDING).update(status=Pickup.DECLINED)


def list_again(listing, by, available_until, today=None):
    """Sellers with a steady supply (a weekly pile of sawdust) re-post in one click."""
    if by != listing.business:
        raise RuleError("Only the seller can list this again.")
    if is_live(listing, today):
        raise RuleError("This listing is still live. Edit it instead.")
    return Listing.objects.create(
        business=listing.business, material=listing.material, quantity=listing.quantity,
        unit=listing.unit, condition=listing.condition, price_per_unit=listing.price_per_unit,
        available_until=available_until, description=listing.description,
    )


def completed_pickups(business):
    """A simple trust signal shown next to each business name."""
    return (Pickup.objects.filter(status=Pickup.COLLECTED, listing__business=business).count()
            + Pickup.objects.filter(status=Pickup.COLLECTED, need__business=business).count())


def impact():
    """Kilos reused (collected) in total and by material, for the impact summary."""
    collected = Pickup.objects.filter(status=Pickup.COLLECTED)
    rows = list(collected.values("listing__material__name").annotate(total=Sum("kg")).order_by("-total"))
    biggest = rows[0]["total"] if rows else 0
    by_material = [{"material": r["listing__material__name"], "kg": r["total"],
                    "bar_percent": round(100 * r["total"] / biggest)} for r in rows]
    return {"total_kg": _sum(collected), "pickups": collected.count(), "by_material": by_material}
