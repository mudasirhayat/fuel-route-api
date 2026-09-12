"""API tests with the external geocoding/routing services mocked out."""
from unittest.mock import patch

from django.test import TestCase

from routes.services.stations import all_stations


def fake_geocode(query):
    lookup = {
        "denver, co": (39.7392, -104.9903, "Denver, Colorado, United States"),
        "chicago, il": (41.8781, -87.6298, "Chicago, Illinois, United States"),
    }
    return lookup[query.strip().lower()]


def fake_route(start, finish):
    # Straight-ish line Denver -> Chicago, ~1000 miles, 20 points.
    points = [
        (
            start[0] + (finish[0] - start[0]) * i / 19,
            start[1] + (finish[1] - start[1]) * i / 19,
        )
        for i in range(20)
    ]
    return points, 1003.0, 15.5 * 3600


class RouteApiTests(TestCase):
    @patch("routes.views.get_route", side_effect=fake_route)
    @patch("routes.views.geocode", side_effect=fake_geocode)
    def test_route_plan_end_to_end(self, mock_geocode, mock_route):
        response = self.client.get(
            "/api/route/", {"start": "Denver, CO", "finish": "Chicago, IL"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["route"]["distance_miles"], 1003.0)
        self.assertGreaterEqual(body["fuel_plan"]["stop_count"], 1)
        self.assertGreater(body["fuel_plan"]["total_fuel_cost_usd"], 0)
        # Purchased fuel covers exactly the miles beyond the first full tank.
        purchased_miles = body["fuel_plan"]["total_gallons_purchased"] * 10
        self.assertAlmostEqual(purchased_miles, 1003.0 - 500, delta=2.0)
        self.assertIn("map_url", body)
        for stop in body["fuel_plan"]["stops"]:
            self.assertLessEqual(stop["detour_miles"], 10.0)

        # The map link returned by the API must render.
        map_path = body["map_url"].split("testserver")[1]
        map_response = self.client.get(map_path)
        self.assertEqual(map_response.status_code, 200)
        self.assertContains(map_response, "leaflet")

    def test_missing_params_rejected(self):
        response = self.client.get("/api/route/", {"start": "Denver, CO"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json()["error"])

    def test_coordinate_inputs_skip_geocoding(self):
        with patch("routes.views.get_route", side_effect=fake_route):
            with patch("routes.services.geocoding.requests.get") as mock_http:
                response = self.client.get(
                    "/api/route/",
                    {"start": "39.7392,-104.9903", "finish": "41.8781,-87.6298"},
                )
        self.assertEqual(response.status_code, 200)
        mock_http.assert_not_called()

    def test_station_catalogue_loads(self):
        stations = all_stations()
        self.assertGreater(len(stations), 6000)
        sample = stations[0]
        self.assertTrue(-125 < sample.lon < -60)
        self.assertTrue(15 < sample.lat < 72)
