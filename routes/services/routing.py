"""Driving directions from the free public OSRM server (OpenStreetMap data).

Exactly one routing call is made per start/finish pair; the decoded result is
cached so repeated requests for the same pair make zero external calls.
"""
import polyline
import requests
from django.core.cache import cache

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
CACHE_TTL = 60 * 60 * 6
METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    pass


def get_route(start, finish):
    """Return (points, total_miles, duration_seconds) between two (lat, lon)
    pairs. points is a list of (lat, lon) tuples along the road geometry."""
    cache_key = f"route:{start[0]:.5f},{start[1]:.5f}:{finish[0]:.5f},{finish[1]:.5f}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = f"{OSRM_URL}/{start[1]},{start[0]};{finish[1]},{finish[0]}"
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "polyline"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError("No drivable route found between these locations.")

    route = data["routes"][0]
    points = polyline.decode(route["geometry"])  # [(lat, lon), ...]
    total_miles = route["distance"] / METERS_PER_MILE
    value = (points, total_miles, route["duration"])
    cache.set(cache_key, value, CACHE_TTL)
    return value
