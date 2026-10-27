"""Real-world scenarios run against the rules in services.py."""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase

from . import services as s
from .models import Area, Business, Listing, Material, Need, Pickup

TODAY = date(2026, 10, 27)
AREAS = {"Gikomba": (-1.2867, 36.8415), "Kamukunji": (-1.2867, 36.8399), "Eastleigh": (-1.2734, 36.8471),
         "Ngara": (-1.2735, 36.8286), "Kariobangi": (-1.2619, 36.8808), "Kilimani": (-1.2850, 36.7867),
         "Kawangware": (-1.2785, 36.7516), "Ruaraka": (-1.2458, 36.8775)}


class Scenario(TestCase):
    def setUp(self):
        self.area = {n: Area.objects.create(name=n, latitude=a, longitude=b) for n, (a, b) in AREAS.items()}
        self.sawdust = Material.objects.create(name="Sawdust", kg_per_sack=12)
        self.cotton = Material.objects.create(name="Cotton offcuts", kg_per_sack=20)
        self.synthetic = Material.objects.create(name="Synthetic offcuts", kg_per_sack=20)

    def biz(self, name, area, kind="carpenter"):
        user = User.objects.create_user(name.lower().replace(" ", ""), password="x-Pass-2026")
        return Business.objects.create(user=user, name=name, kind=kind, area=self.area[area], phone="0700000000")

    def listing(self, seller, material, quantity, unit="kg", days=7, **kw):
        return Listing.objects.create(business=seller, material=material, quantity=quantity, unit=unit,
                                      available_until=TODAY + timedelta(days=days), **kw)

    def carpenters(self):
        self.kamukunji = self.biz("Kamukunji Furniture Works", "Kamukunji")
        self.joinery = self.biz("Jua Kali Joinery", "Kamukunji")
        self.bedmakers = self.biz("Gikomba Bed Makers", "Gikomba")
        self.doors = self.biz("Kariobangi Doors", "Kariobangi")
        self.sofas = self.biz("Ngara Sofa Makers", "Ngara")
        self.far = self.biz("Kawangware Timber", "Kawangware")
        self.l_kamukunji = self.listing(self.kamukunji, self.sawdust, 25, unit="sack")   # 300 kg
        self.l_joinery = self.listing(self.joinery, self.sawdust, 180)
        self.l_bed = self.listing(self.bedmakers, self.sawdust, 250)
        self.l_doors = self.listing(self.doors, self.sawdust, 220)
        self.l_sofas = self.listing(self.sofas, self.sawdust, 120)
        self.l_far = self.listing(self.far, self.sawdust, 500)

    # 1. The briquette maker who needs 1,200 kg
    def test_briquette_maker_needs_1200_kg(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=1200, max_km=10,
                                   needed_by=TODAY + timedelta(days=4))
        self.assertEqual(self.l_kamukunji.kg, 300, "25 sacks at 12 kg each")
        p = s.plan(need, TODAY)
        names = [r["listing"].business.name for r in p["suggestions"]]
        self.assertEqual(names[0], "Kariobangi Doors", "same area comes first")
        self.assertNotIn("Kawangware Timber", names, "outside 10 km")
        self.assertEqual(p["covered_kg"], 1070)
        pickups = s.ask_all(need, moto, TODAY)
        self.assertEqual(len(pickups), 5)
        self.assertEqual(s.kg_still_needed(need), 130)
        # Each seller answers on their own screen
        for pk in pickups[:4]:
            s.accept(pk, pk.seller)
        s.decline(pickups[4], pickups[4].seller)
        self.assertEqual(s.kg_still_needed(need), 130 + pickups[4].kg, "a decline frees that amount to ask elsewhere")
        again = [r["listing"].business.name for r in s.plan(need, TODAY)["suggestions"]]
        self.assertNotIn(pickups[4].seller.name, again, "a seller who said no isn't suggested again")
        need.max_km = 20
        self.assertIn("Kawangware Timber", [r["listing"].business.name for r in s.plan(need, TODAY)["suggestions"]],
                      "a wider distance finds more")
        # The buyer does the collection round and confirms each stop
        for pk in pickups[:4]:
            s.mark_collected(Pickup.objects.get(pk=pk.pk), moto)
        need.refresh_from_db()
        self.assertEqual(s.kg_collected(need), sum(pk.kg for pk in pickups[:4]))
        self.assertFalse(need.closed, "still short, so the need stays open")

    # 2. A small buyer takes part of a big listing
    def test_partial_quantity_leaves_the_rest_for_others(self):
        self.carpenters()
        poultry = self.biz("Mama Njeri Poultry", "Gikomba", kind="poultry farmer")
        need = Need.objects.create(business=poultry, material=self.sawdust, kg=60, max_km=5, needed_by=TODAY)
        pk = s.ask_for_pickup(need, self.l_bed, 60, poultry, TODAY)
        s.accept(pk, self.bedmakers)
        self.assertEqual(s.kg_left(self.l_bed), 190)
        self.assertTrue(s.is_live(self.l_bed, TODAY), "190 kg is still on offer")

    # 3. Two buyers want the same pile
    def test_second_buyer_is_declined_when_the_pile_runs_out(self):
        self.carpenters()
        a = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        b = self.biz("Fuel Bora", "Kariobangi", kind="briquette maker")
        c = self.biz("Kuku Farm", "Kariobangi", kind="poultry farmer")
        na = Need.objects.create(business=a, material=self.sawdust, kg=200, needed_by=TODAY)
        nb = Need.objects.create(business=b, material=self.sawdust, kg=100, needed_by=TODAY)
        nc = Need.objects.create(business=c, material=self.sawdust, kg=20, needed_by=TODAY)
        pa = s.ask_for_pickup(na, self.l_doors, 200, a, TODAY)
        pb = s.ask_for_pickup(nb, self.l_doors, 100, b, TODAY)
        pc = s.ask_for_pickup(nc, self.l_doors, 20, c, TODAY)
        _, auto_declined = s.accept(pa, self.doors)
        self.assertEqual(auto_declined, 1)
        pb.refresh_from_db()
        pc.refresh_from_db()
        self.assertEqual(pb.status, Pickup.DECLINED, "100 kg no longer fits in the 20 kg left")
        self.assertEqual(pc.status, Pickup.PENDING, "20 kg still fits")

    # 4. No-show: the seller cancels and the kilos come back
    def test_cancel_after_accept_returns_kilos(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=300, needed_by=TODAY)
        pk = s.ask_for_pickup(need, self.l_kamukunji, 300, moto, TODAY)
        s.accept(pk, self.kamukunji)
        self.assertFalse(s.is_live(self.l_kamukunji, TODAY))
        s.cancel(Pickup.objects.get(pk=pk.pk), self.kamukunji)
        self.assertTrue(s.is_live(self.l_kamukunji, TODAY))
        self.assertEqual(s.kg_still_needed(need), 300)

    # 5. Collecting everything closes the need
    def test_need_closes_when_fully_collected(self):
        self.carpenters()
        poultry = self.biz("Mama Njeri Poultry", "Gikomba", kind="poultry farmer")
        need = Need.objects.create(business=poultry, material=self.sawdust, kg=100, max_km=3, needed_by=TODAY)
        for pk in s.ask_all(need, poultry, TODAY):
            s.accept(pk, pk.seller)
            s.mark_collected(Pickup.objects.get(pk=pk.pk), poultry)
        need.refresh_from_db()
        self.assertTrue(need.closed)
        self.assertEqual(s.completed_pickups(poultry), 1)

    # 6. The right business, and only the right business
    def test_permissions(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=TODAY)
        with self.assertRaisesMessage(s.RuleError, "Only the business that posted"):
            s.ask_for_pickup(need, self.l_doors, 100, self.joinery, TODAY)
        pk = s.ask_for_pickup(need, self.l_doors, 100, moto, TODAY)
        with self.assertRaisesMessage(s.RuleError, "Only the seller can accept"):
            s.accept(pk, moto)
        with self.assertRaisesMessage(s.RuleError, "Only agreed pickups"):
            s.mark_collected(pk, moto)
        with self.assertRaisesMessage(s.RuleError, "can't cancel"):
            s.cancel(pk, self.doors)
        s.accept(pk, self.doors)
        with self.assertRaisesMessage(s.RuleError, "Only the buyer can mark"):
            s.mark_collected(Pickup.objects.get(pk=pk.pk), self.doors)

    # 7. Things that must never show up as matches
    def test_hidden_listings(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        own = self.listing(moto, self.sawdust, 50)
        expired = self.listing(self.joinery, self.sawdust, 70, days=-1)
        withdrawn = self.listing(self.joinery, self.sawdust, 80, withdrawn=True)
        cotton = self.listing(self.joinery, self.cotton, 90)
        need = Need.objects.create(business=moto, material=self.sawdust, kg=5000, max_km=10, needed_by=TODAY)
        shown = {r["listing"].id for r in s.find_matches(need, TODAY)}
        for hidden in (own, expired, withdrawn, cotton, self.l_far):
            self.assertNotIn(hidden.id, shown)

    # 8. Guard rails on amounts
    def test_amount_rules(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=TODAY)
        with self.assertRaisesMessage(s.RuleError, "Only 220 kg"):
            s.ask_for_pickup(need, self.l_doors, 500, moto, TODAY)
        with self.assertRaisesMessage(s.RuleError, "only need 100 kg"):
            s.ask_for_pickup(need, self.l_doors, 150, moto, TODAY)
        s.ask_for_pickup(need, self.l_doors, 100, moto, TODAY)
        with self.assertRaisesMessage(s.RuleError, "already asked"):
            s.ask_for_pickup(need, self.l_doors, 10, moto, TODAY)

    # 9. Cotton buyers don't get synthetic offcuts
    def test_cotton_only_buyer(self):
        tailor = self.biz("Amani Tailors", "Eastleigh", kind="tailor")
        self.listing(tailor, self.synthetic, 60)
        cotton = self.listing(tailor, self.cotton, 22)
        studio = self.biz("Pamba Upcycle Studio", "Kilimani", kind="upcycler")
        need = Need.objects.create(business=studio, material=self.cotton, kg=40, needed_by=TODAY)
        self.assertEqual([r["listing"].id for r in s.find_matches(need, TODAY)], [cotton.id])

    # 10. A steady weekly supply
    def test_list_again(self):
        self.carpenters()
        last_week = self.listing(self.kamukunji, self.sawdust, 25, unit="sack", days=-1)
        with self.assertRaisesMessage(s.RuleError, "still live"):
            s.list_again(self.l_kamukunji, self.kamukunji, TODAY + timedelta(days=7), TODAY)
        again = s.list_again(last_week, self.kamukunji, TODAY + timedelta(days=7), TODAY)
        self.assertEqual((again.kg, again.unit, again.quantity), (300, "sack", 25))

    # 11. Closing a need cancels unanswered asks
    def test_close_need(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=400, needed_by=TODAY)
        first, second = s.ask_all(need, moto, TODAY)[:2]
        s.accept(first, first.seller)
        self.assertEqual(s.close_need(need, moto), 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (Pickup.ACCEPTED, Pickup.CANCELLED))

    # 12. Withdrawing a listing declines waiting requests, keeps agreed ones
    def test_withdraw(self):
        self.carpenters()
        a = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        b = self.biz("Fuel Bora", "Kariobangi", kind="briquette maker")
        pa = s.ask_for_pickup(Need.objects.create(business=a, material=self.sawdust, kg=100, needed_by=TODAY),
                              self.l_doors, 100, a, TODAY)
        pb = s.ask_for_pickup(Need.objects.create(business=b, material=self.sawdust, kg=50, needed_by=TODAY),
                              self.l_doors, 50, b, TODAY)
        s.accept(pa, self.doors)
        with self.assertRaisesMessage(s.RuleError, "Only the seller"):
            s.withdraw(self.l_doors, a)
        self.assertEqual(s.withdraw(self.l_doors, self.doors), 1)
        pa.refresh_from_db()
        pb.refresh_from_db()
        self.assertEqual((pa.status, pb.status), (Pickup.ACCEPTED, Pickup.DECLINED))
