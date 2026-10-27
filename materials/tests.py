"""Real-world scenarios run against the rules in services.py, then against the API."""
from datetime import date, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
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


class Api(Fixtures, APITestCase):
    """The same rules through the HTTP endpoints, plus logins, privacy and status codes."""
    # The views use the real date, so build listings relative to it.
    today = timezone.localdate()

    def as_business(self, business):
        self.client.force_authenticate(user=business.user)

    def post_need(self, kg=1200, max_km=10):
        response = self.client.post("/api/needs/", {"material": self.sawdust.id, "kg": kg, "max_km": max_km,
                                                    "needed_by": self.today.isoformat()})
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def test_public_endpoints_and_login_required(self):
        self.carpenters()
        for url in ["/api/areas/", "/api/materials/", "/api/listings/", "/api/impact/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        listings = self.client.get("/api/listings/")
        kamukunji = next(row for row in listings.data if row["id"] == self.l_kamukunji.id)
        self.assertEqual(kamukunji["kg_left"], 300)
        self.assertNotIn("0700000000", listings.content.decode(), "no phone numbers on public listings")
        self.assertEqual(self.client.get("/api/needs/").status_code, 401)
        self.assertEqual(self.client.post("/api/listings/", {}).status_code, 401)

    def test_browse_hides_expired_withdrawn_and_filters_by_material_and_area(self):
        self.carpenters()
        expired = self.listing(self.joinery, self.sawdust, 70, days=-1)
        withdrawn = self.listing(self.joinery, self.sawdust, 80, withdrawn=True)
        cotton = self.listing(self.joinery, self.cotton, 90)
        ids = [row["id"] for row in self.client.get(f"/api/listings/?material={self.sawdust.id}").data]
        self.assertEqual(len(ids), 6, "the six live sawdust listings")
        for hidden in (expired, withdrawn, cotton):
            self.assertNotIn(hidden.id, ids)
        in_ngara = self.client.get(f"/api/listings/?area={self.area['Ngara'].id}").data
        self.assertEqual([row["id"] for row in in_ngara], [self.l_sofas.id])
        self.assertEqual(self.client.get("/api/listings/?material=abc").status_code, 200, "bad filters are ignored")

    def test_register_creates_user_and_business_with_hashed_password(self):
        data = {"username": "moto", "password": "Briquettes-2026",
                "business": {"name": "Moto Briquettes", "kind": "briquette maker",
                             "area": self.area["Kariobangi"].id, "phone": "0722600100"}}
        response = self.client.post("/api/register/", data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("password", response.data)
        user = User.objects.get(username="moto")
        self.assertNotEqual(user.password, "Briquettes-2026")
        self.assertTrue(user.check_password("Briquettes-2026"))
        self.assertEqual(user.business.area, self.area["Kariobangi"])
        self.assertEqual(self.client.post("/api/register/", data, format="json").status_code, 400, "username taken")
        weak = {**data, "username": "other", "password": "123"}
        self.assertEqual(self.client.post("/api/register/", weak, format="json").status_code, 400)
        # The new account works with Basic auth straight away (moto:Briquettes-2026 in base64).
        self.client.credentials(HTTP_AUTHORIZATION="Basic bW90bzpCcmlxdWV0dGVzLTIwMjY=")
        self.assertEqual(self.client.get("/api/profile/").data["name"], "Moto Briquettes")

    def test_create_listing_converts_sacks_and_checks_input(self):
        self.carpenters()
        self.as_business(self.joinery)
        good = {"material": self.sawdust.id, "quantity": 25, "unit": "sack",
                "available_until": self.today.isoformat()}
        response = self.client.post("/api/listings/", good)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kg"], 300)
        past = {**good, "available_until": (self.today - timedelta(days=1)).isoformat()}
        self.assertEqual(self.client.post("/api/listings/", past).status_code, 400)
        self.assertEqual(self.client.post("/api/listings/", {**good, "material": 9999}).status_code, 400)
        self.assertEqual(self.client.post("/api/listings/", {**good, "quantity": 0}).status_code, 400)

    def test_only_the_owner_can_change_a_listing(self):
        self.carpenters()
        self.as_business(self.joinery)
        url = f"/api/listings/{self.l_doors.id}/"
        self.assertEqual(self.client.patch(url, {"quantity": 1}).status_code, 404)
        self.assertEqual(self.client.post(url + "withdraw/").status_code, 404)
        self.as_business(self.doors)
        self.assertEqual(self.client.post(url + "withdraw/").status_code, 200)
        ids = [row["id"] for row in self.client.get("/api/listings/").data]
        self.assertNotIn(self.l_doors.id, ids, "a withdrawn listing disappears from browse")
        self.assertEqual(self.client.post(url + "again/").status_code, 201)

    def test_listing_cannot_shrink_below_promised_kilos(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need = Need.objects.create(business=moto, material=self.sawdust, kg=200, needed_by=self.today)
        s.accept(s.ask_for_pickup(need, self.l_doors, 200, moto), self.doors)
        self.as_business(self.doors)
        response = self.client.patch(f"/api/listings/{self.l_doors.id}/", {"quantity": 10})
        self.assertEqual(response.status_code, 400)
        self.assertIn("200 kg is already promised", str(response.data))

    def test_worked_example_through_the_api(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        self.as_business(moto)
        need_id = self.post_need()
        matches = self.client.get(f"/api/needs/{need_id}/matches/").data
        names = [row["listing"]["business"]["name"] for row in matches["suggestions"]]
        self.assertEqual(len(names), 5)
        self.assertEqual(names[0], "Kariobangi Doors")
        self.assertNotIn("Kawangware Timber", names)
        self.assertEqual(matches["covered_kg"], 1070)
        self.assertEqual(self.client.post(f"/api/needs/{need_id}/ask-all/").status_code, 201)
        self.assertEqual(self.client.get(f"/api/needs/{need_id}/").data["progress"]["still_needed"], 130)

        # Ngara Sofa Makers says no: its 120 kg is free to ask elsewhere, and it isn't suggested again.
        sofas_pickup = Pickup.objects.get(listing=self.l_sofas)
        self.as_business(self.sofas)
        self.assertEqual(self.client.post(f"/api/pickups/{sofas_pickup.id}/decline/").status_code, 200)
        self.as_business(moto)
        matches = self.client.get(f"/api/needs/{need_id}/matches/").data
        self.assertEqual(matches["still_needed"], 250)
        self.assertEqual(matches["suggestions"], [], "nobody else within 10 km")
        self.assertEqual(self.client.post(f"/api/needs/{need_id}/distance/", {"max_km": 15}).status_code, 400)
        self.client.post(f"/api/needs/{need_id}/distance/", {"max_km": 20})
        names = [row["listing"]["business"]["name"]
                 for row in self.client.get(f"/api/needs/{need_id}/matches/").data["suggestions"]]
        self.assertEqual(names, ["Kawangware Timber"], "a wider distance finds more, and not Ngara again")

    def test_needs_are_private_and_validated(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        self.as_business(moto)
        need_id = self.post_need()
        bad_distance = {"material": self.sawdust.id, "kg": 100, "max_km": 7, "needed_by": self.today.isoformat()}
        self.assertEqual(self.client.post("/api/needs/", bad_distance).status_code, 400)
        past = {**bad_distance, "max_km": 10, "needed_by": (self.today - timedelta(days=1)).isoformat()}
        self.assertEqual(self.client.post("/api/needs/", past).status_code, 400)
        self.as_business(self.joinery)
        for url in [f"/api/needs/{need_id}/", f"/api/needs/{need_id}/matches/"]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(f"/api/needs/{need_id}/close/").status_code, 404)
        self.assertEqual(self.client.get("/api/needs/").data, [], "another business sees none of moto's needs")

    def test_pickup_permissions_and_phone_privacy(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        moto.phone = "0722600100"
        moto.save()
        self.as_business(moto)
        need_id = self.post_need(kg=200, max_km=5)
        asked = self.client.post(f"/api/needs/{need_id}/ask/", {"listing": self.l_bed.id, "kg": 200})
        self.assertEqual(asked.status_code, 201, asked.data)
        pickup_id = asked.data["pickup"]["id"]
        self.assertIsNone(asked.data["pickup"]["other_phone"], "no phone before agreeing")
        response = self.client.post(f"/api/pickups/{pickup_id}/accept/")
        self.assertEqual((response.status_code, response.data["detail"]), (400, "Only the seller can accept."))

        self.as_business(self.joinery)
        self.assertEqual(self.client.post(f"/api/pickups/{pickup_id}/accept/").status_code, 404, "a stranger")
        self.as_business(self.bedmakers)
        self.assertEqual(self.client.post(f"/api/pickups/{pickup_id}/explode/").status_code, 404)
        accepted = self.client.post(f"/api/pickups/{pickup_id}/accept/")
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("0722600100", accepted.data["message"])
        self.assertEqual(accepted.data["pickup"]["other_phone"], "0722600100", "the seller now sees the buyer")
        self.assertEqual(self.client.post(f"/api/pickups/{pickup_id}/collected/").status_code, 400)

        self.as_business(moto)
        mine = self.client.get("/api/pickups/").data
        self.assertEqual(mine[0]["other_phone"], "0700000000", "the buyer now sees the seller")
        self.assertEqual(self.client.post(f"/api/pickups/{pickup_id}/collected/").status_code, 200)
        self.assertTrue(Need.objects.get(pk=need_id).closed)
        impact = self.client.get("/api/impact/").data
        self.assertEqual((impact["total_kg"], impact["pickups"]), (200, 1))

    def test_accepting_declines_requests_that_no_longer_fit(self):
        self.carpenters()
        a = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        b = self.biz("Fuel Bora", "Kariobangi", kind="briquette maker")
        pa = s.ask_for_pickup(Need.objects.create(business=a, material=self.sawdust, kg=200, needed_by=self.today),
                              self.l_doors, 200, a)
        pb = s.ask_for_pickup(Need.objects.create(business=b, material=self.sawdust, kg=100, needed_by=self.today),
                              self.l_doors, 100, b)
        self.as_business(self.doors)
        response = self.client.post(f"/api/pickups/{pa.id}/accept/")
        self.assertIn("1 other request(s) no longer fit", response.data["message"])
        self.assertEqual(response.data["listing_kg_left"], 20)
        pb.refresh_from_db()
        self.assertEqual(pb.status, Pickup.DECLINED)

    def test_asking_for_too_much_returns_the_rule_message(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        self.as_business(moto)
        need_id = self.post_need(kg=1000)
        response = self.client.post(f"/api/needs/{need_id}/ask/", {"listing": self.l_doors.id, "kg": 500})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"detail": "Only 220 kg is left on that listing."})
        not_a_number = self.client.post(f"/api/needs/{need_id}/ask/", {"listing": self.l_doors.id, "kg": "ten"})
        self.assertEqual(not_a_number.status_code, 400)


class LoadBasics(TestCase):
    def test_running_twice_creates_no_duplicates(self):
        call_command("load_basics", stdout=StringIO())
        call_command("load_basics", stdout=StringIO())
        self.assertEqual((Area.objects.count(), Material.objects.count()), (20, 8))
