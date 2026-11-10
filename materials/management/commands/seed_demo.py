"""Demo businesses (each with a login), listings and some finished pickups. Run load_basics first."""
import os
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from materials import services
from materials.models import Area, Business, Listing, Material, Need, Pickup

# username, business name, kind, area, phone. Every demo login uses DEMO_PASSWORD from .env.
BUSINESSES = [
    ("doors", "Kariobangi Doors and Frames", "carpenter", "Kariobangi", "0712345001"),
    ("kamukunji", "Kamukunji Furniture Works", "carpenter", "Kamukunji", "0712345002"),
    ("joinery", "Jua Kali Joinery", "carpenter", "Kamukunji", "0712345003"),
    ("bedmakers", "Gikomba Bed Makers", "carpenter", "Gikomba", "0712345004"),
    ("sofas", "Ngara Sofa Makers", "upholsterer", "Ngara", "0712345005"),
    ("timber", "Kawangware Timber Yard", "timber yard", "Kawangware", "0712345007"),
    ("tailors", "Amani Tailors", "tailor", "Eastleigh", "0712345008"),
    ("fruit", "Marikiti Fruit Traders", "market traders", "Marikiti", "0712345009"),
    ("moto", "Moto Briquettes", "briquette maker", "Kariobangi", "0722600100"),
    ("poultry", "Mama Njeri Poultry", "poultry farmer", "Gikomba", "0722600200"),
    ("pamba", "Pamba Upcycle Studio", "upcycler", "Kilimani", "0722600300"),
    ("bsf", "Ruaraka Insect Protein", "insect farm", "Ruaraka", "0722600400"),
]

# username, material, quantity, unit, condition, price per unit, description
LISTINGS = [
    ("doors", "Sawdust", 220, "kg", "mixed", 0, "Mostly cypress. Bring your own sacks."),
    ("kamukunji", "Sawdust", 25, "sack", "sorted", 50, "Dry, no shavings. Collect after 4 pm."),
    ("joinery", "Sawdust", 180, "kg", "mixed", 0, ""),
    ("bedmakers", "Sawdust", 250, "kg", "mixed", 0, "Pile behind the workshop."),
    ("sofas", "Sawdust", 10, "sack", "mixed", 0, ""),
    ("timber", "Sawdust", 500, "kg", "sorted", 3, "Every week."),
    ("tailors", "Cotton offcuts", 3, "sack", "sorted", 0, "Sorted by colour."),
    ("tailors", "Synthetic offcuts", 60, "kg", "mixed", 0, ""),
    ("fruit", "Market food waste", 4, "sack", "mixed", 0, "Every morning before 9."),
]

# Finished pickups from past weeks: buyer, seller, material, kg, days ago. 1,215 kg in total.
HISTORY = [
    ("poultry", "bedmakers", "Sawdust", 60, 20), ("poultry", "joinery", "Sawdust", 40, 13),
    ("moto", "doors", "Sawdust", 200, 12), ("moto", "kamukunji", "Sawdust", 300, 12),
    ("moto", "doors", "Sawdust", 180, 5), ("moto", "sofas", "Sawdust", 120, 5),
    ("pamba", "tailors", "Cotton offcuts", 35, 9), ("bsf", "fruit", "Market food waste", 160, 3),
    ("bsf", "fruit", "Market food waste", 120, 2),
]


class Command(BaseCommand):
    help = "Rebuild the 12 demo businesses and logins, their listings and past pickups. Nothing else is touched."

    @transaction.atomic
    def handle(self, *args, **options):
        password = os.environ.get("DEMO_PASSWORD")
        if not password:
            raise CommandError("Set DEMO_PASSWORD in .env first. Every demo login uses it.")
        if not Area.objects.exists() or not Material.objects.exists():
            raise CommandError("Run python manage.py load_basics first.")

        # Start fresh: deleting the demo businesses and logins also deletes their listings, needs and pickups.
        Business.objects.filter(name__in=[row[1] for row in BUSINESSES]).delete()
        User.objects.filter(username__in=[row[0] for row in BUSINESSES]).delete()

        # Hashing a password is slow on purpose (seconds each), so hash DEMO_PASSWORD once and give every
        # demo login the same stored hash. Each still logs in with DEMO_PASSWORD as normal.
        password_hash = make_password(password)
        today = timezone.localdate()
        business = {}
        for username, name, kind, area, phone in BUSINESSES:
            user = User.objects.create(username=username, password=password_hash)
            business[username] = Business.objects.create(user=user, name=name, kind=kind,
                                                         area=Area.objects.get(name=area), phone=phone)

        # Past pickups sit on old, expired listings and closed needs, so they show on the impact
        # page and in the trust numbers without taking kilos from today's listings.
        for buyer, seller, material, kg, days_ago in HISTORY:
            day = today - timedelta(days=days_ago)
            created = timezone.now() - timedelta(days=days_ago + 1)
            old = Listing.objects.create(business=business[seller], material=Material.objects.get(name=material),
                                         quantity=kg, available_until=day, created_at=created)
            need = Need.objects.create(business=business[buyer], material=old.material, kg=kg, needed_by=day,
                                       closed=True, created_at=created)
            Pickup.objects.create(listing=old, need=need, kg=kg, status=Pickup.COLLECTED)

        for username, material, quantity, unit, condition, price, description in LISTINGS:
            Listing.objects.create(business=business[username], material=Material.objects.get(name=material),
                                   quantity=quantity, unit=unit, condition=condition, price_per_unit=price,
                                   description=description, available_until=today + timedelta(days=7))

        self.stdout.write(f"{len(BUSINESSES)} businesses, {len(LISTINGS)} live listings and "
                          f"{services.impact()['total_kg']} kg of past pickups ready.")
        self.stdout.write("Log in with DEMO_PASSWORD as: " + ", ".join(row[0] for row in BUSINESSES))
        self.stdout.write("Buyer for the demo: moto. Sellers: doors, kamukunji, joinery, bedmakers, sofas "
                          f"(timber is outside 10 km). Sawdust is material {Material.objects.get(name='Sawdust').id}.")
