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


class ApiHelpers(Fixtures):
    """Shortcuts for calling the API as one business. Shared by Api and CrudTests."""
    # The views use the real date, so build listings relative to it.
    today = timezone.localdate()

    def login_as(self, business):
        # force_authenticate skips the token step; AuthTests checks real tokens.
        self.client.force_authenticate(user=business.user if business else None)

    def get(self, url, business=None):
        self.login_as(business)
        return self.client.get(url)

    def post(self, url, business, **data):
        self.login_as(business)
        return self.client.post(url, data, format="json")

    def patch(self, url, business, **data):
        self.login_as(business)
        return self.client.patch(url, data, format="json")

    def delete(self, url, business):
        self.login_as(business)
        return self.client.delete(url)

    def need_page(self, need_id, business):
        return self.get(f"/api/needs/{need_id}/", business)

    def suggestions(self, need_id, business):
        return self.get(f"/api/needs/{need_id}/suggestions/", business).data

    def post_need(self, buyer, kg, max_km=10):
        response = self.post("/api/needs/", buyer, material=self.sawdust.id, kg=kg, max_km=max_km,
                             needed_by=self.today.isoformat())
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def ask(self, buyer, need_id, listing, kg):
        return self.post("/api/pickups/", buyer, need=need_id, listing=listing.id, kg=kg)


class Api(ApiHelpers, APITestCase):
    """The same rules through the HTTP endpoints, logged in as the acting business."""

    def test_worked_example_end_to_end(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 1200)

        found = self.suggestions(need_id, moto)
        names = [row["seller"]["name"] for row in found["suggestions"]]
        self.assertEqual(len(names), 5)
        self.assertEqual(names[0], "Kariobangi Doors", "same area comes first")
        self.assertNotIn("Kawangware Timber", names, "outside 10 km")
        self.assertEqual(found["covered_kg"], 1070)

        self.assertEqual(self.post(f"/api/needs/{need_id}/ask-all/", moto).status_code, 201)
        self.assertEqual(self.need_page(need_id, moto).data["progress"]["still_needed"], 130)

        # Each seller answers: Ngara Sofa Makers declines, the other four agree.
        for pickup in Pickup.objects.filter(need_id=need_id):
            action = "decline" if pickup.listing == self.l_sofas else "accept"
            response = self.post(f"/api/pickups/{pickup.id}/{action}/", pickup.seller)
            self.assertEqual(response.status_code, 200, response.data)
        found = self.suggestions(need_id, moto)
        self.assertEqual(found["still_needed"], 250, "the declined 120 kg is free to ask elsewhere")
        self.assertEqual(found["suggestions"], [], "nobody else within 10 km, and Ngara isn't asked again")

        bad = self.patch(f"/api/needs/{need_id}/", moto, max_km=15)
        self.assertEqual((bad.status_code, bad.data), (400, {"max_km": ["Pick 5, 10 or 20 km."]}))
        self.assertEqual(self.patch(f"/api/needs/{need_id}/", moto, max_km=20).status_code, 200)
        names = [row["seller"]["name"] for row in self.suggestions(need_id, moto)["suggestions"]]
        self.assertEqual(names, ["Kawangware Timber"], "a wider distance finds more")

        doors_pickup = Pickup.objects.get(need_id=need_id, listing=self.l_doors)
        self.assertEqual(self.post(f"/api/pickups/{doors_pickup.id}/collect/", moto).status_code, 200)
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
        response = self.post(f"{url}/accept/", moto)
        self.assertEqual((response.status_code, response.data), (403, {"detail": "Only the seller can accept."}))
        self.assertEqual(self.post(f"{url}/accept/", self.joinery).status_code, 404, "not part of this pickup")
        self.assertEqual(self.post(f"{url}/accept/", self.doors).status_code, 200)
        response = self.post(f"{url}/collect/", self.doors)
        self.assertEqual((response.status_code, response.data),
                         (403, {"detail": "Only the buyer can mark a pickup as collected."}))
        self.assertEqual(self.post(f"{url}/collect/", moto).status_code, 200)
        self.assertEqual(self.need_page(need.id, self.joinery).status_code, 404, "only the buyer opens a need")

    def test_no_phone_before_agreeing(self):
        self.carpenters()
        self.bedmakers.phone = "0711000111"
        self.bedmakers.save()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        moto.phone = "0722600100"
        moto.save()
        self.assertNotIn("0711000111", self.client.get("/api/listings/").content.decode(), "never on listings")
        self.assertNotIn("0711000111", self.client.get("/api/businesses/").content.decode(), "never on profiles")

        need_id = self.post_need(moto, 200, max_km=5)
        asked = self.ask(moto, need_id, self.l_bed, 200)
        self.assertEqual(asked.status_code, 201, asked.data)
        self.assertIsNone(asked.data["other_phone"])
        self.assertNotIn("0711000111", self.need_page(need_id, moto).content.decode())
        dashboard = self.get("/api/me/", self.bedmakers)
        self.assertEqual(len(dashboard.data["waiting_for_answer"]), 1)
        self.assertNotIn("0722600100", dashboard.content.decode())

        accepted = self.post(f"/api/pickups/{asked.data['id']}/accept/", self.bedmakers)
        self.assertIn("0722600100", accepted.data["message"])
        self.assertIn("0711000111", self.need_page(need_id, moto).content.decode(), "the buyer now sees the seller")
        dashboard = self.get("/api/me/", self.bedmakers).data
        self.assertEqual(dashboard["agreed_pickups"][0]["other_phone"], "0722600100", "and the seller the buyer")

    def test_impact_counts_only_collected_kilos(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 300)
        first = self.ask(moto, need_id, self.l_doors, 200).data["id"]
        second = self.ask(moto, need_id, self.l_bed, 100).data["id"]
        self.post(f"/api/pickups/{first}/accept/", self.doors)
        self.post(f"/api/pickups/{second}/accept/", self.bedmakers)
        self.assertEqual(self.client.get("/api/impact/").data["total_kg"], 0, "agreed isn't collected")
        self.post(f"/api/pickups/{first}/collect/", moto)
        impact = self.client.get("/api/impact/").data
        self.assertEqual((impact["total_kg"], impact["pickups"]), (200, 1))
        self.assertEqual(impact["by_material"], [{"material": "Sawdust", "kg": 200, "bar_percent": 100}])


class CrudTests(ApiHelpers, APITestCase):
    """Phase 2: every edit and delete rule, every owner-only check, phone privacy and pagination."""

    def test_reference_lists_are_public_paginated_and_never_show_a_phone(self):
        self.carpenters()
        for url in ["/api/areas/", "/api/materials/", "/api/businesses/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(set(response.data), {"count", "next", "previous", "results"}, url)
        profiles = self.client.get("/api/businesses/")
        self.assertEqual(profiles.data["count"], 6)
        self.assertNotIn("phone", profiles.data["results"][0])
        self.assertNotIn("0700000000", profiles.content.decode())
        profile = self.client.get(f"/api/businesses/{self.doors.id}/")
        self.assertEqual((profile.status_code, profile.data["name"]), (200, "Kariobangi Doors"))
        self.assertNotIn("phone", profile.data)
        # PATCH /api/me/ edits my own business (and only that one).
        self.assertEqual(self.patch("/api/me/", self.doors, phone="0799000111").data["phone"], "0799000111")

    def test_browse_matches_is_live_and_filters(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        expired = self.listing(self.joinery, self.sawdust, 70, days=-1)
        withdrawn = self.listing(self.joinery, self.sawdust, 80, withdrawn=True)
        cotton = self.listing(self.joinery, self.cotton, 90)
        # Doors is fully promised (not live); Gikomba is partly promised (still live with 190 kg).
        doors_need = Need.objects.create(business=moto, material=self.sawdust, kg=220, needed_by=self.today)
        s.accept(s.ask_for_pickup(doors_need, self.l_doors, 220, moto), self.doors)
        bed_need = Need.objects.create(business=moto, material=self.sawdust, kg=60, needed_by=self.today)
        s.accept(s.ask_for_pickup(bed_need, self.l_bed, 60, moto), self.bedmakers)

        browse = self.client.get("/api/listings/").data
        shown = {row["id"] for row in browse["results"]}
        expected = {listing.id for listing in Listing.objects.all() if s.is_live(listing)}
        self.assertEqual(shown, expected, "the database filter agrees with services.is_live()")
        self.assertEqual(browse["count"], len(expected))
        for hidden in (expired, withdrawn, self.l_doors):
            self.assertNotIn(hidden.id, shown)
        self.assertIn(cotton.id, shown)
        sawdust_only = self.client.get(f"/api/listings/?material={self.sawdust.id}").data["results"]
        self.assertNotIn(cotton.id, {row["id"] for row in sawdust_only})
        in_ngara = self.client.get(f"/api/listings/?area={self.area['Ngara'].id}").data["results"]
        self.assertEqual([row["id"] for row in in_ngara], [self.l_sofas.id])
        # One listing by id can be any listing, live or not.
        self.assertEqual(self.client.get(f"/api/listings/{expired.id}/").status_code, 200)

    def test_listing_create_and_edit_rules(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        new = {"material": self.sawdust.id, "quantity": 25, "unit": "sack", "available_until": self.today.isoformat()}
        self.login_as(None)
        self.assertEqual(self.client.post("/api/listings/", new, format="json").status_code, 401)
        created = self.post("/api/listings/", self.joinery, **new)
        self.assertEqual((created.status_code, created.data["kg"]), (201, 300))
        url = f"/api/listings/{created.data['id']}/"

        self.login_as(None)
        self.assertEqual(self.client.patch(url, {"quantity": 30}, format="json").status_code, 401)
        response = self.patch(url, moto, quantity=30)
        self.assertEqual((response.status_code, response.data["detail"]), (403, "Only the seller can edit this listing."))
        response = self.patch(url, self.joinery, quantity=30, description="Dry")
        self.assertEqual((response.status_code, response.data["kg"], response.data["description"]), (200, 360, "Dry"))
        past = (self.today - timedelta(days=1)).isoformat()
        self.assertEqual(self.patch(url, self.joinery, available_until=past).status_code, 400)
        self.assertEqual(self.patch(url, self.joinery, material=self.cotton.id).status_code, 200,
                         "nobody has asked yet, so the material can change")

        need = Need.objects.create(business=moto, material=self.sawdust, kg=200, needed_by=self.today)
        s.ask_for_pickup(need, self.l_doors, 200, moto)
        response = self.patch(f"/api/listings/{self.l_doors.id}/", self.doors, material=self.cotton.id)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "The material can't change once buyers have asked for this listing."))
        s.accept(Pickup.objects.get(listing=self.l_doors), self.doors)
        response = self.patch(f"/api/listings/{self.l_doors.id}/", self.doors, quantity=100)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "200 kg is already promised to buyers, so the listing can't be smaller than that."))

    def test_listing_delete_withdraw_and_relist(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        doors_need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=self.today)
        s.accept(s.ask_for_pickup(doors_need, self.l_doors, 100, moto), self.doors)
        bed_need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=self.today)
        waiting = s.ask_for_pickup(bed_need, self.l_bed, 100, moto)

        response = self.delete(f"/api/listings/{self.l_bed.id}/", moto)
        self.assertEqual((response.status_code, response.data["detail"]), (403, "Only the seller can delete this listing."))
        response = self.delete(f"/api/listings/{self.l_doors.id}/", self.doors)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "Some of this listing is agreed or collected. Withdraw it instead."))
        self.assertEqual(self.delete(f"/api/listings/{self.l_bed.id}/", self.bedmakers).status_code, 204)
        self.assertFalse(Pickup.objects.filter(pk=waiting.pk).exists(), "waiting requests go with it")

        url = f"/api/listings/{self.l_joinery.id}"
        self.assertEqual(self.post(f"{url}/withdraw/", moto).status_code, 403)
        response = self.post(f"{url}/relist/", self.joinery)
        self.assertEqual((response.status_code, response.data["detail"]), (400, "This listing is still live. Edit it instead."))
        self.assertEqual(self.post(f"{url}/withdraw/", self.joinery).status_code, 200)
        relisted = self.post(f"{url}/relist/", self.joinery)
        self.assertEqual((relisted.status_code, relisted.data["listing"]["kg"]), (201, 180))

    def test_need_edit_and_delete_rules_and_owner_only(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        others = Need.objects.create(business=self.joinery, material=self.sawdust, kg=50, needed_by=self.today)
        need_id = self.post_need(moto, 300)
        mine = self.get("/api/needs/", moto).data
        self.assertEqual([row["id"] for row in mine["results"]], [need_id], "only my own needs")
        for response in (self.get(f"/api/needs/{others.id}/", moto), self.patch(f"/api/needs/{others.id}/", moto, kg=10),
                         self.delete(f"/api/needs/{others.id}/", moto)):
            self.assertEqual(response.status_code, 404, "someone else's need doesn't exist for you")

        url = f"/api/needs/{need_id}/"
        self.assertEqual(self.patch(url, moto, max_km=7).status_code, 400)
        past = (self.today - timedelta(days=1)).isoformat()
        self.assertEqual(self.patch(url, moto, needed_by=past).status_code, 400)
        self.assertEqual(self.patch(url, moto, max_km=20, note="Dry only").data["max_km"], 20)
        self.ask(moto, need_id, self.l_doors, 200)
        response = self.patch(url, moto, kg=100)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "You've already asked for 200 kg, so the need can't be smaller than that."))
        response = self.patch(url, moto, material=self.cotton.id)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "The material can't change once you've asked sellers for it."))

        s.accept(Pickup.objects.get(need_id=need_id), self.doors)
        response = self.delete(url, moto)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (400, "Some of this need is agreed or collected. Close it instead."))
        self.assertEqual(self.post(f"{url}close/", moto).status_code, 200)
        response = self.patch(url, moto, note="again")
        self.assertEqual((response.status_code, response.data["detail"]), (400, "This need is closed."))

        waiting_need = self.post_need(moto, 50)
        self.ask(moto, waiting_need, self.l_bed, 50)
        self.assertEqual(self.delete(f"/api/needs/{waiting_need}/", moto).status_code, 204)
        self.assertFalse(Pickup.objects.filter(need_id=waiting_need).exists(), "its waiting requests go with it")

    def test_suggestions_shape(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        found = self.suggestions(self.post_need(moto, 1200), moto)
        self.assertEqual(set(found), {"need", "still_needed", "covered_kg", "suggestions"})
        first = found["suggestions"][0]
        self.assertEqual(set(first), {"listing", "seller", "km", "kg_left", "suggested_kg"})
        self.assertEqual(first["listing"], {"id": self.l_doors.id, "material": {"id": self.sawdust.id, "name": "Sawdust"}})
        self.assertEqual(first["seller"], {"id": self.doors.id, "name": "Kariobangi Doors",
                                           "area": {"id": self.area["Kariobangi"].id, "name": "Kariobangi"}})
        self.assertEqual((first["km"], first["kg_left"], first["suggested_kg"]), (0.0, 220, 220))

    def test_pickup_create_edit_delete_and_cancel_rules(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 300)
        response = self.ask(self.joinery, need_id, self.l_doors, 100)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (403, "Only the business that posted this need can ask for pickups."))
        asked = self.ask(moto, need_id, self.l_doors, 200)
        self.assertEqual(asked.status_code, 201, asked.data)
        url = f"/api/pickups/{asked.data['id']}/"

        self.assertEqual(self.get("/api/pickups/", moto).data["count"], 1, "the buyer sees it")
        self.assertEqual(self.get("/api/pickups/", self.doors).data["count"], 1, "the seller sees it")
        self.assertEqual(self.get("/api/pickups/", self.joinery).data["count"], 0, "nobody else does")
        self.assertEqual(self.get(url, self.joinery).status_code, 404)
        self.assertEqual(self.get("/api/pickups/?status=accepted", moto).data["count"], 0)

        response = self.patch(url, self.doors, kg=150)
        self.assertEqual((response.status_code, response.data["detail"]), (403, "Only the buyer can change this request."))
        self.assertEqual(self.patch(url, moto, kg=150).data["kg"], 150)
        self.assertEqual(self.patch(url, moto, kg=0).data["detail"], "Ask for at least 1 kg.")
        self.assertEqual(self.patch(url, moto, kg=250).data["detail"], "Only 220 kg is left on that listing.")
        self.ask(moto, need_id, self.l_bed, 150)
        self.assertEqual(self.patch(url, moto, kg=200).data["detail"], "You can ask this seller for at most 150 kg.")

        response = self.post(f"{url}cancel/", self.doors)
        self.assertEqual((response.status_code, response.data["detail"]),
                         (403, "Only the buyer can cancel a waiting request."))
        response = self.delete(url, self.doors)
        self.assertEqual((response.status_code, response.data["detail"]), (403, "Only the buyer can delete this request."))
        self.assertEqual(self.post(f"{url}accept/", self.doors).status_code, 200)
        self.assertEqual(self.patch(url, moto, kg=100).data["detail"],
                         "Only waiting requests can be changed. Cancel it and ask again instead.")
        self.assertEqual(self.delete(url, moto).data["detail"],
                         "Only waiting requests can be deleted. Cancel an agreed pickup instead.")
        self.assertEqual(self.post(f"{url}cancel/", self.doors).status_code, 200, "either side cancels an agreed pickup")
        self.assertEqual(self.post(f"{url}cancel/", moto).data["detail"], "You can't cancel this pickup.")

        bed_pickup = Pickup.objects.get(listing=self.l_bed)
        self.assertEqual(self.delete(f"/api/pickups/{bed_pickup.id}/", moto).status_code, 204)
        self.assertFalse(Pickup.objects.filter(pk=bed_pickup.pk).exists())


def keys_anywhere(data):
    """Every dictionary key in a JSON response, however deeply nested."""
    if isinstance(data, dict):
        return set(data) | set().union(*(keys_anywhere(value) for value in data.values()))
    if isinstance(data, list):
        return set().union(*(keys_anywhere(item) for item in data))
    return set()


class JsonShapeTests(ApiHelpers, APITestCase):
    """Phase 3: the JSON the React app gets back is the documented shape, and never leaks a phone."""
    AREA = {"id", "name"}
    BUSINESS = {"id", "name", "area"}
    LISTING = {"id", "seller", "material", "quantity", "unit", "kg", "kg_left", "condition", "price_per_unit",
               "available_until", "description", "is_live", "created_at"}
    NEED = {"id", "business", "material", "kg", "max_km", "needed_by", "note", "closed", "progress", "created_at"}
    PICKUP = {"id", "status", "status_label", "kg", "listing", "buyer", "seller", "other_phone",
              "created_at", "updated_at"}

    def test_listings_and_public_profiles_never_include_a_phone(self):
        self.carpenters()
        self.doors.phone = "0711222333"
        self.doors.save()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        # Even with an agreed pickup on the listing, its public views stay phone-free.
        need = Need.objects.create(business=moto, material=self.sawdust, kg=100, needed_by=self.today)
        s.accept(s.ask_for_pickup(need, self.l_doors, 100, moto), self.doors)
        for url in ["/api/listings/", f"/api/listings/{self.l_doors.id}/",
                    "/api/businesses/", f"/api/businesses/{self.doors.id}/"]:
            for viewer in (None, moto, self.doors):
                response = self.get(url, viewer)
                self.assertEqual(response.status_code, 200, url)
                self.assertNotIn("phone", keys_anywhere(response.data), url)
                self.assertNotIn("other_phone", keys_anywhere(response.data), url)
                self.assertNotIn("0711222333", response.content.decode(), url)

    def test_listing_shape(self):
        self.carpenters()
        page = self.client.get("/api/listings/").data
        self.assertEqual(set(page), {"count", "next", "previous", "results"})
        listing = self.client.get(f"/api/listings/{self.l_kamukunji.id}/").data
        self.assertEqual(set(listing), self.LISTING)
        self.assertEqual(listing["seller"], {"id": self.kamukunji.id, "name": "Kamukunji Furniture Works",
                                             "area": {"id": self.area["Kamukunji"].id, "name": "Kamukunji"}})
        self.assertEqual(listing["material"], {"id": self.sawdust.id, "name": "Sawdust"})
        self.assertEqual((listing["quantity"], listing["unit"], listing["kg"], listing["kg_left"], listing["is_live"]),
                         (25, "sack", 300, 300, True))

    def test_need_and_pickup_shapes(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 300)
        created = self.get(f"/api/needs/{need_id}/", moto).data
        self.assertEqual(set(created), self.NEED | {"pickups"})
        self.assertEqual(set(created["business"]), self.BUSINESS)
        self.assertEqual(set(created["business"]["area"]), self.AREA)
        self.assertEqual(created["progress"], {"collected": 0, "agreed": 0, "waiting": 0, "still_needed": 300})
        self.assertEqual(set(self.get("/api/needs/", moto).data["results"][0]), self.NEED)

        pickup = self.ask(moto, need_id, self.l_doors, 200).data
        self.assertEqual(set(pickup), self.PICKUP)
        self.assertEqual(pickup["listing"], {"id": self.l_doors.id, "kg_left": 220,
                                             "material": {"id": self.sawdust.id, "name": "Sawdust"}})
        self.assertEqual((pickup["status"], pickup["status_label"], pickup["other_phone"]),
                         ("pending", "Waiting for the seller", None))
        self.assertEqual(set(pickup["buyer"]), self.BUSINESS)
        self.assertEqual(set(self.get("/api/pickups/", moto).data), {"count", "next", "previous", "results"})
        need_page = self.get(f"/api/needs/{need_id}/", moto).data
        self.assertEqual([row["id"] for row in need_page["pickups"]], [pickup["id"]])
        self.assertEqual(need_page["progress"]["waiting"], 200)

    def test_action_responses_are_a_message_and_the_resource(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        need_id = self.post_need(moto, 1200)
        asked = self.post(f"/api/needs/{need_id}/ask-all/", moto).data
        self.assertEqual(set(asked), {"message", "pickups"})
        self.assertEqual(len(asked["pickups"]), 5)

        doors_pickup = next(row["id"] for row in asked["pickups"] if row["seller"]["id"] == self.doors.id)
        sofas_pickup = next(row["id"] for row in asked["pickups"] if row["seller"]["id"] == self.sofas.id)
        bed_pickup = next(row["id"] for row in asked["pickups"] if row["seller"]["id"] == self.bedmakers.id)
        self.post(f"/api/pickups/{bed_pickup}/accept/", self.bedmakers)
        accepted = self.post(f"/api/pickups/{doors_pickup}/accept/", self.doors).data
        self.assertEqual(set(accepted), {"message", "pickup"})
        self.assertEqual(accepted["pickup"]["listing"]["kg_left"], 0, "the agreed kilos are taken off")
        self.assertEqual(set(self.post(f"/api/pickups/{sofas_pickup}/decline/", self.sofas).data), {"message", "pickup"})
        self.assertEqual(set(self.post(f"/api/pickups/{doors_pickup}/collect/", moto).data), {"message", "pickup"})

        withdrawn = self.post(f"/api/listings/{self.l_far.id}/withdraw/", self.far).data
        self.assertEqual((set(withdrawn), set(withdrawn["listing"])), ({"message", "listing"}, self.LISTING))
        relisted = self.post(f"/api/listings/{self.l_far.id}/relist/", self.far).data
        self.assertEqual(set(relisted), {"message", "listing"})
        closed = self.post(f"/api/needs/{need_id}/close/", moto).data
        self.assertEqual((set(closed), closed["need"]["closed"]), ({"message", "need"}, True))
        cancelled = self.post(f"/api/pickups/{bed_pickup}/cancel/", moto)
        self.assertEqual((set(cancelled.data), cancelled.data["pickup"]["status"]), ({"message", "pickup"}, "cancelled"))

    def test_choice_errors_are_clear(self):
        self.carpenters()
        moto = self.biz("Moto Briquettes", "Kariobangi", kind="briquette maker")
        new = {"material": self.sawdust.id, "quantity": 5, "available_until": self.today.isoformat()}
        self.assertEqual(self.post("/api/listings/", self.joinery, **new, unit="bag").data,
                         {"unit": ['Unit must be "kg" or "sack".']})
        self.assertEqual(self.post("/api/listings/", self.joinery, **new, condition="clean").data,
                         {"condition": ['Condition must be "sorted" or "mixed".']})
        need = {"material": self.sawdust.id, "kg": 100, "needed_by": self.today.isoformat()}
        self.assertEqual(self.post("/api/needs/", moto, **need, max_km=7).data, {"max_km": ["Pick 5, 10 or 20 km."]})

    def test_docs_are_public(self):
        schema = self.client.get("/api/schema/")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("ByProduct API", schema.content.decode())
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)


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
        self.assertEqual(self.client.get("/api/listings/").data["count"], 9)
        self.assertEqual(self.client.get("/api/impact/").data["total_kg"], 1215)

    def test_demo_need_finds_the_five_carpenters(self):
        call_command("seed_demo", stdout=StringIO())
        self.client.force_authenticate(user=User.objects.get(username="moto"))
        sawdust = Material.objects.get(name="Sawdust")
        response = self.client.post("/api/needs/", {"material": sawdust.id, "kg": 1200, "max_km": 10,
                                                    "needed_by": timezone.localdate().isoformat()})
        found = self.client.get(f"/api/needs/{response.data['id']}/suggestions/").data
        self.assertEqual(len(found["suggestions"]), 5)
        self.assertEqual(found["covered_kg"], 1070)
        self.assertNotIn("Kawangware Timber Yard", [row["seller"]["name"] for row in found["suggestions"]])

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
