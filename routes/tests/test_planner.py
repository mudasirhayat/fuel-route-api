"""Unit tests for the fuel stop planner (pure logic, no network)."""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from routes.services.planner import Candidate, PlanningError, plan_stops


def make_candidate(mile, price, name="S"):
    station = SimpleNamespace(
        id=int(mile * 10),
        name=f"{name}@{mile}",
        address="addr",
        city="city",
        state="TX",
        price=price,
        lat=0.0,
        lon=0.0,
        as_dict=lambda: {"name": f"{name}@{mile}", "price": price},
    )
    return Candidate(station=station, mile=float(mile), detour_miles=1.0)


@override_settings(FUEL_MAX_RANGE_MILES=500, FUEL_VEHICLE_MPG=10, FUEL_MAX_DETOUR_MILES=10)
class PlanStopsTests(SimpleTestCase):
    def test_short_trip_needs_no_stops(self):
        stops, totals = plan_stops([make_candidate(100, 3.0)], total_miles=400)
        self.assertEqual(stops, [])
        self.assertEqual(totals["total_fuel_cost_usd"], 0)
        self.assertEqual(totals["trip_fuel_burned_gallons"], 40.0)

    def test_single_stop_buys_just_enough(self):
        # 700 mile trip, full 500-mile tank: needs 200 miles = 20 gallons.
        stops, totals = plan_stops([make_candidate(300, 3.0)], total_miles=700)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0]["gallons_purchased"], 20.0)
        self.assertAlmostEqual(totals["total_fuel_cost_usd"], 60.0)

    def test_prefers_cheaper_station(self):
        # Both stations can cover the trip; the cheap one must win.
        candidates = [make_candidate(200, 4.0), make_candidate(400, 2.0)]
        stops, totals = plan_stops(candidates, total_miles=800)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["price_per_gallon"], 2.0)
        self.assertAlmostEqual(totals["total_fuel_cost_usd"], 2.0 * 30.0)

    def test_buys_minimum_at_expensive_station_to_reach_cheap_one(self):
        # Expensive station at 450, cheap at 700; 1100 mile trip.
        # Optimal: at 450 buy just enough to reach 700 (250mi - 50mi left
        # in tank = 200mi = 20gal), then fill what is needed at the cheap one.
        candidates = [make_candidate(450, 5.0), make_candidate(700, 2.0)]
        stops, totals = plan_stops(candidates, total_miles=1100)
        self.assertEqual(len(stops), 2)
        self.assertAlmostEqual(stops[0]["gallons_purchased"], 20.0)
        self.assertEqual(stops[0]["price_per_gallon"], 5.0)
        self.assertAlmostEqual(stops[1]["gallons_purchased"], 40.0)
        self.assertEqual(stops[1]["price_per_gallon"], 2.0)
        self.assertAlmostEqual(totals["total_fuel_cost_usd"], 100.0 + 80.0)

    def test_final_topup_happens_at_cheaper_station_near_destination(self):
        # A naive planner buys the final top-up at mile 480; the optimal plan
        # bridges to the cheaper station at 520 and buys the rest there.
        candidates = [make_candidate(480, 4.0), make_candidate(520, 2.5)]
        stops, totals = plan_stops(candidates, total_miles=900)
        self.assertEqual(stops[-1]["price_per_gallon"], 2.5)
        # 20 miles bought at 480 to reach 520, 380 miles bought at 520.
        self.assertAlmostEqual(stops[0]["gallons_purchased"], 2.0)
        self.assertAlmostEqual(stops[1]["gallons_purchased"], 38.0)

    def test_gap_longer_than_range_raises(self):
        candidates = [make_candidate(100, 3.0), make_candidate(900, 3.0)]
        with self.assertRaises(PlanningError):
            plan_stops(candidates, total_miles=1200)

    def test_no_stations_at_all_raises(self):
        with self.assertRaises(PlanningError):
            plan_stops([], total_miles=1200)

    def test_multi_stop_long_trip_totals_consistent(self):
        candidates = [make_candidate(m, 3.0 + (m % 7) * 0.1) for m in range(100, 2400, 120)]
        stops, totals = plan_stops(candidates, total_miles=2400)
        # Fuel purchased must cover exactly the miles beyond the first tank.
        purchased_miles = sum(s["gallons_purchased"] for s in stops) * 10
        self.assertAlmostEqual(purchased_miles, 2400 - 500, delta=1.0)
        self.assertGreater(totals["stop_count"], 2)
