"""Real-world scenarios run against the rules in services.py, then against the API."""
import os
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from . import services as s
from .models import Area, Business, Listing, Material, Need, Pickup

TODAY = date(2026, 10, 27)
AREAS = {"Gikomba": (-1.2867, 36.8415), "Kamukunji": (-1.2867, 36.8399), "Eastleigh": (-1.2734, 36.8471),
         "Ngara": (-1.2735, 36.8286), "Kariobangi": (-1.2619, 36.8808), "Kilimani": (-1.2850, 36.7867),
         "Kawangware": (-1.2785, 36.7516), "Ruaraka": (-1.2458, 36.8775)}


class Fixtures:
    """Areas, materials and the worked example's carpenters, shared by the rule tests and the API tests."""
    today = TODAY

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
                                      available_until=self.today + timedelta(days=days), **kw)

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


class Scenario(Fixtures, TestCase):
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
        with self.assertRaisesMessage(s.NotAllowed, "Only the buyer can cancel a waiting request."):
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


class Api(Fixtures, APITestCase):
    """The same rules through the HTTP endpoints, logged in as the acting business."""
    # The views use the real date, so build listings relative to it.
    today = timezone.localdate()

    def post(self, url, business, **data):
        self.client.force_authenticate(user=business.user)
        return self.client.post(url, data, format="json")

    def get(self, url, business):
        self.client.force_authenticate(user=business.user)
        return self.client.get(url)

    def need_page(self, need_id, business):
        return self.get(f"/api/needs/{need_id}/", business)

    def post_need(self, buyer, kg, max_km=10):
        response = self.post("/api/needs/", buyer, material=self.sawdust.id, kg=kg, max_km=max_km,
                             needed_by=self.today.isoformat())
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def test_worked_example_end_to_end(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 1200)

        page = self.need_page(need_id, moto).data
        names = [row["seller"] for row in page["suggestions"]]
        self.assertEqual(len(names), 5)
        self.assertEqual(names[0], "Kariobangi Doors", "same area comes first")
        self.assertNotIn("Kawangware Timber", names, "outside 10 km")
        self.assertEqual(page["covered_kg"], 1070)

        self.assertEqual(self.post(f"/api/needs/{need_id}/ask-all/", moto).status_code, 201)
        self.assertEqual(self.need_page(need_id, moto).data["progress"]["still_needed"], 130)

        # Each seller answers: Ngara Sofa Makers declines, the other four agree.
        for pickup in Pickup.objects.filter(need_id=need_id):
            action = "decline" if pickup.listing == self.l_sofas else "accept"
            response = self.post(f"/api/pickups/{pickup.id}/{action}/", pickup.seller)
            self.assertEqual(response.status_code, 200, response.data)
        page = self.need_page(need_id, moto).data
        self.assertEqual(page["progress"]["still_needed"], 250, "the declined 120 kg is free to ask elsewhere")
        self.assertEqual(page["suggestions"], [], "nobody else within 10 km, and Ngara isn't asked again")

        bad = self.post(f"/api/needs/{need_id}/distance/", moto, max_km=15)
        self.assertEqual(bad.data, {"detail": "Pick 5, 10 or 20 km."})
        self.post(f"/api/needs/{need_id}/distance/", moto, max_km=20)
        names = [row["seller"] for row in self.need_page(need_id, moto).data["suggestions"]]
        self.assertEqual(names, ["Kawangware Timber"], "a wider distance finds more")

        doors_pickup = Pickup.objects.get(need_id=need_id, listing=self.l_doors)
        self.assertEqual(self.post(f"/api/pickups/{doors_pickup.id}/collected/", moto).status_code, 200)
        impact = self.client.get("/api/impact/").data
        self.assertEqual((impact["total_kg"], impact["pickups"]), (220, 1))

    def test_no_token_returns_401(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=self.today)
        pickup = s.ask_for_pickup(need, self.l_doors, 100, moto)
        for response in [
            self.client.post("/api/needs/", {"material": self.sawdust.id, "kg": 100}, format="json"),
            self.client.post("/api/listings/", {"material": self.sawdust.id, "quantity": 5}, format="json"),
            self.client.get(f"/api/needs/{need.id}/"),
            self.client.post(f"/api/pickups/{pickup.id}/accept/", {}, format="json"),
        ]:
            self.assertEqual(response.status_code, 401)

    def test_wrong_business_cannot_accept_or_mark_collected(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=self.today)
        pickup = s.ask_for_pickup(need, self.l_doors, 100, moto)
        url = f"/api/pickups/{pickup.id}"
        for wrong in (moto, self.joinery):
            response = self.post(f"{url}/accept/", wrong)
            self.assertEqual((response.status_code, response.data), (403, {"detail": "Only the seller can accept."}))
        self.assertEqual(self.post(f"{url}/accept/", self.doors).status_code, 200)
        for wrong in (self.doors, self.joinery):
            response = self.post(f"{url}/collected/", wrong)
            self.assertEqual((response.status_code, response.data),
                             (403, {"detail": "Only the buyer can mark a pickup as collected."}))
        self.assertEqual(self.post(f"{url}/collected/", moto).status_code, 200)
        self.assertEqual(self.need_page(need.id, self.joinery).status_code, 404, "only the buyer opens a need")

    def test_no_phone_before_agreeing(self):
        self.carpenters()
        self.bedmakers.phone = "0711000111"
        self.bedmakers.save()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        moto.phone = "0722600100"
        moto.save()
        self.assertNotIn("0711000111", self.client.get("/api/listings/").content.decode(), "never on listings")

        need_id = self.post_need(moto, 200, max_km=5)
        asked = self.post(f"/api/needs/{need_id}/ask/", moto, listing=self.l_bed.id, kg=200)
        self.assertEqual(asked.status_code, 201, asked.data)
        self.assertIsNone(asked.data["pickup"]["other_phone"])
        self.assertNotIn("0711000111", self.need_page(need_id, moto).content.decode())
        dashboard = self.get("/api/me/", self.bedmakers)
        self.assertEqual(len(dashboard.data["waiting_for_answer"]), 1)
        self.assertNotIn("0722600100", dashboard.content.decode())

        accepted = self.post(f"/api/pickups/{asked.data['pickup']['id']}/accept/", self.bedmakers)
        self.assertIn("0722600100", accepted.data["message"])
        self.assertIn("0711000111", self.need_page(need_id, moto).content.decode(), "the buyer now sees the seller")
        dashboard = self.get("/api/me/", self.bedmakers).data
        self.assertEqual(dashboard["agreed_pickups"][0]["other_phone"], "0722600100", "and the seller the buyer")

    def test_impact_counts_only_collected_kilos(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 300)
        first = self.post(f"/api/needs/{need_id}/ask/", moto, listing=self.l_doors.id, kg=200).data["pickup"]["id"]
        second = self.post(f"/api/needs/{need_id}/ask/", moto, listing=self.l_bed.id, kg=100).data["pickup"]["id"]
        self.post(f"/api/pickups/{first}/accept/", self.doors)
        self.post(f"/api/pickups/{second}/accept/", self.bedmakers)
        self.assertEqual(self.client.get("/api/impact/").data["total_kg"], 0, "agreed isn't collected")
        self.post(f"/api/pickups/{first}/collected/", moto)
        impact = self.client.get("/api/impact/").data
        self.assertEqual((impact["total_kg"], impact["pickups"]), (200, 1))
        self.assertEqual(impact["by_material"], [{"material": "Sawdust", "kg": 200, "bar_percent": 100}])


class LoadBasics(TestCase):
    def test_running_twice_creates_no_duplicates(self):
        call_command("load_basics", stdout=StringIO())
        call_command("load_basics", stdout=StringIO())
        self.assertEqual((Area.objects.count(), Material.objects.count()), (20, 8))


@mock.patch.dict(os.environ, {"DEMO_PASSWORD": "Demo-Pass-2026"})
class SeedDemo(APITestCase):
    def setUp(self):
        call_command("load_basics", stdout=StringIO())

    def test_running_twice_is_safe(self):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Business.objects.count(), 12)
        self.assertEqual(User.objects.count(), 12)
        self.assertTrue(User.objects.get(username="moto").check_password("Demo-Pass-2026"))
        self.assertEqual(len(self.client.get("/api/listings/").data), 9)
        self.assertEqual(self.client.get("/api/impact/").data["total_kg"], 1215)

    def test_demo_need_finds_the_five_carpenters(self):
        call_command("seed_demo", stdout=StringIO())
        self.client.force_authenticate(user=User.objects.get(username="moto"))
        sawdust = Material.objects.get(name="Sawdust")
        response = self.client.post("/api/needs/", {"material": sawdust.id, "kg": 1200, "max_km": 10,
                                                    "needed_by": timezone.localdate().isoformat()})
        page = self.client.get(f"/api/needs/{response.data['id']}/").data
        self.assertEqual(len(page["suggestions"]), 5)
        self.assertEqual(page["covered_kg"], 1070)
        self.assertNotIn("Kawangware Timber Yard", [row["seller"] for row in page["suggestions"]])

    def test_needs_demo_password(self):
        with mock.patch.dict(os.environ, {"DEMO_PASSWORD": ""}):
            with self.assertRaisesMessage(CommandError, "Set DEMO_PASSWORD in .env first."):
                call_command("seed_demo", stdout=StringIO())

    def test_needs_load_basics_first(self):
        Area.objects.all().delete()
        with self.assertRaisesMessage(CommandError, "Run python manage.py load_basics first."):
            call_command("seed_demo", stdout=StringIO())


class AuthTests(APITestCase):
    """How the React app logs in: register or log in, send the Bearer token, refresh it, log out."""

    def setUp(self):
        self.kariobangi = Area.objects.create(name="Kariobangi", latitude=-1.2619, longitude=36.8808)
        self.sawdust = Material.objects.create(name="Sawdust", kg_per_sack=12)
        self.signup = {"username": "moto", "password": "Briquettes-2026", "name": "Moto Briquettes",
                       "kind": "briquette maker", "area": self.kariobangi.id, "phone": "0722600100"}

    def register(self, **changes):
        return self.client.post("/api/auth/register/", {**self.signup, **changes}, format="json")

    def login(self, username="moto", password="Briquettes-2026"):
        return self.client.post("/api/auth/login/", {"username": username, "password": password}, format="json")

    def bearer(self, access):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_register_returns_tokens_and_stores_a_hashed_password(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(set(response.data), {"access", "refresh", "business"})
        self.assertEqual(response.data["business"]["name"], "Moto Briquettes")
        self.assertNotIn("Briquettes-2026", response.content.decode())
        user = User.objects.get(username="moto")
        self.assertNotEqual(user.password, "Briquettes-2026")
        self.assertTrue(user.check_password("Briquettes-2026"))
        self.assertEqual(user.business.area, self.kariobangi)
        self.assertEqual(self.register().status_code, 400, "username taken")
        weak = self.register(username="other", password="123")
        self.assertEqual(weak.status_code, 400)
        self.assertIn("password", weak.data)

    def test_login_returns_tokens_and_business_and_rejects_a_wrong_password(self):
        self.register()
        response = self.login()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {"access", "refresh", "business"})
        self.assertEqual(response.data["business"]["name"], "Moto Briquettes")
        self.assertEqual(self.login(password="wrong-password").status_code, 401)

    def test_protected_endpoint_needs_a_bearer_token(self):
        self.register()
        self.assertEqual(self.client.get("/api/me/").status_code, 401)
        self.bearer(self.login().data["access"])
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business"]["name"], "Moto Briquettes")
        self.bearer("not-a-real-token")
        response = self.client.get("/api/me/")
        self.assertEqual((response.status_code, response.data["code"]), (401, "token_not_valid"))

    def test_refresh_rotates_and_rejects_the_old_refresh_token(self):
        self.register()
        old = self.login().data["refresh"]
        response = self.client.post("/api/auth/refresh/", {"refresh": old}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(set(response.data), {"access", "refresh"})
        new = response.data["refresh"]
        self.assertNotEqual(new, old)
        self.assertEqual(self.client.post("/api/auth/refresh/", {"refresh": old}, format="json").status_code, 401,
                         "the old refresh token was blacklisted when it rotated")
        self.assertEqual(self.client.post("/api/auth/refresh/", {"refresh": new}, format="json").status_code, 200)

    def test_logout_blacklists_the_refresh_token(self):
        self.register()
        refresh = self.login().data["refresh"]
        self.assertEqual(self.client.post("/api/auth/logout/", {"refresh": refresh}, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/auth/refresh/", {"refresh": refresh}, format="json").status_code, 401)

    @override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:5173"])
    def test_cors_allows_only_the_react_app(self):
        preflight = {"HTTP_ACCESS_CONTROL_REQUEST_METHOD": "GET",
                     "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "authorization"}
        allowed = self.client.options("/api/me/", HTTP_ORIGIN="http://localhost:5173", **preflight)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        other = self.client.options("/api/me/", HTTP_ORIGIN="https://other-site.example", **preflight)
        self.assertNotIn("Access-Control-Allow-Origin", other.headers)

    def test_wrong_business_gets_403(self):
        buyer = self.register().data
        seller = self.register(username="doors", name="Kariobangi Doors", kind="carpenter").data
        listing = Listing.objects.create(business_id=seller["business"]["id"], material=self.sawdust,
                                         quantity=220, available_until=timezone.localdate())
        need = Need.objects.create(business_id=buyer["business"]["id"], material=self.sawdust, kg=100,
                                   needed_by=timezone.localdate())
        pickup = s.ask_for_pickup(need, listing, 100, need.business)
        self.bearer(buyer["access"])
        response = self.client.post(f"/api/pickups/{pickup.id}/accept/")
        self.assertEqual((response.status_code, response.data), (403, {"detail": "Only the seller can accept."}))
        self.bearer(seller["access"])
        self.assertEqual(self.client.post(f"/api/pickups/{pickup.id}/accept/").status_code, 200)

    def test_a_login_without_a_business_gets_403(self):
        self.client.force_authenticate(user=User.objects.create_user("admin", password="x-Pass-2026"))
        response = self.client.get("/api/me/")
        self.assertEqual((response.status_code, response.data["detail"]), (403, "This account has no business profile."))

    def test_delete_account_unless_something_was_agreed(self):
        buyer = self.register().data
        seller = self.register(username="doors", name="Kariobangi Doors", kind="carpenter").data
        listing = Listing.objects.create(business_id=seller["business"]["id"], material=self.sawdust,
                                         quantity=220, available_until=timezone.localdate())
        need = Need.objects.create(business_id=buyer["business"]["id"], material=self.sawdust, kg=100,
                                   needed_by=timezone.localdate())
        s.accept(s.ask_for_pickup(need, listing, 100, need.business), listing.business)
        self.bearer(seller["access"])
        response = self.client.delete("/api/me/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "You have agreed or collected pickups, so this account can't be deleted.")
        Pickup.objects.update(status=Pickup.CANCELLED)
        self.assertEqual(self.client.delete("/api/me/").status_code, 204)
        self.assertFalse(User.objects.filter(username="doors").exists())
        self.assertFalse(Business.objects.filter(name="Kariobangi Doors").exists())
        self.assertFalse(Listing.objects.exists(), "the business's listings go with it")
