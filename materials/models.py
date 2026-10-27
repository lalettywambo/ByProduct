from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Area(models.Model):
    """A Nairobi trading area, with its centre point. Loaded once from data/areas.csv."""
    name = models.CharField(max_length=80, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Material(models.Model):
    """A kind of leftover, like sawdust or cotton offcuts. The admin manages this list."""
    name = models.CharField(max_length=80, unique=True)
    kg_per_sack = models.PositiveIntegerField(
        help_text="Rough weight of one full sack, used when people count in sacks.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Business(models.Model):
    """A business on ByProduct. The same business can sell some things and buy others."""
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=80, help_text="For example: tailor, carpenter, briquette maker")
    area = models.ForeignKey(Area, on_delete=models.PROTECT)
    phone = models.CharField(max_length=20, help_text="Shown only to businesses you agree a pickup with.")

    class Meta:
        verbose_name_plural = "businesses"

    def __str__(self):
        return self.name


class Listing(models.Model):
    """Leftovers a seller has on offer."""
    UNITS = [("kg", "kg"), ("sack", "sacks")]
    CONDITIONS = [("sorted", "Clean and sorted"), ("mixed", "Mixed")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="listings")
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="In the unit below")
    unit = models.CharField(max_length=4, choices=UNITS, default="kg")
    kg = models.PositiveIntegerField(editable=False)
    condition = models.CharField(max_length=10, choices=CONDITIONS, default="mixed")
    price_per_unit = models.PositiveIntegerField(default=0, help_text="KES per kg or per sack. 0 means free.")
    available_until = models.DateField()
    description = models.TextField(blank=True)
    withdrawn = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Everything is matched in kilos. Sacks are converted with the material's rough sack weight.
        self.kg = self.quantity * (self.material.kg_per_sack if self.unit == "sack" else 1)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.kg} kg {self.material} from {self.business}"


class Need(models.Model):
    """What a buyer needs: for example 1,200 kg of sawdust within 10 km by Friday."""
    DISTANCES = [(5, "5 km"), (10, "10 km"), (20, "20 km")]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="needs")
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    kg = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    max_km = models.PositiveIntegerField(choices=DISTANCES, default=10)
    needed_by = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.business} needs {self.kg} kg {self.material}"


class Pickup(models.Model):
    """One buyer asking one seller for an amount, and where that stands."""
    PENDING, ACCEPTED, DECLINED, CANCELLED, COLLECTED = "pending", "accepted", "declined", "cancelled", "collected"
    STATUSES = [(PENDING, "Waiting for the seller"), (ACCEPTED, "Agreed"), (DECLINED, "Declined"),
                (CANCELLED, "Cancelled"), (COLLECTED, "Collected")]

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="pickups")
    need = models.ForeignKey(Need, on_delete=models.CASCADE, related_name="pickups")
    kg = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def buyer(self):
        return self.need.business

    @property
    def seller(self):
        return self.listing.business

    def __str__(self):
        return f"{self.buyer} wants {self.kg} kg from {self.seller} ({self.status})"
