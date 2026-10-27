"""Loads the areas from data/areas.csv and the list of materials. Safe to run again."""
import csv

from django.conf import settings
from django.core.management.base import BaseCommand

from materials.models import Area, Material

# Rough weight of one full sack. Example values: check with real sellers and change them in the admin.
MATERIALS = {
    "Sawdust": 12,
    "Wood offcuts": 25,
    "Cotton offcuts": 20,
    "Synthetic offcuts": 20,
    "Denim offcuts": 20,
    "Mixed fabric offcuts": 20,
    "Market food waste": 40,
    "Charcoal dust": 30,
}


class Command(BaseCommand):
    help = "Load areas from data/areas.csv and the materials list."

    def handle(self, *args, **options):
        # update_or_create / get_or_create look up by name first, so a second run never duplicates rows.
        with open(settings.BASE_DIR / "data" / "areas.csv", newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                Area.objects.update_or_create(
                    name=row["name"].strip(),
                    defaults={"latitude": float(row["latitude"]), "longitude": float(row["longitude"])},
                )
        # get_or_create (not update) so sack weights edited in the admin aren't overwritten.
        for name, kg_per_sack in MATERIALS.items():
            Material.objects.get_or_create(name=name, defaults={"kg_per_sack": kg_per_sack})
        self.stdout.write(f"{Area.objects.count()} areas and {Material.objects.count()} materials ready.")
