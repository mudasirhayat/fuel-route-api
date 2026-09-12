"""Geocoding of free-text US locations via Nominatim (OpenStreetMap).

Results are cached, and inputs given directly as "lat,lon" skip the external
call entirely, so repeated or coordinate-based requests cost zero HTTP calls.
"""
import re

import requests
from django.core.cache import cache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "fuel-route-api (backend assessment demo)"
CACHE_TTL = 60 * 60 * 24
_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Continental US bounding box (loose) used to validate inputs.
US_BOUNDS = {"lat": (18.0, 72.0), "lon": (-180.0, -66.0)}


class GeocodingError(Exception):
    pass


def _in_us_bounds(lat, lon):
    return US_BOUNDS["lat"][0] <= lat <= US_BOUNDS["lat"][1] and (
        US_BOUNDS["lon"][0] <= lon <= US_BOUNDS["lon"][1]
    )


def geocode(query):
    """Resolve a location string to (lat, lon, display_name)."""
    match = _COORD_RE.match(query)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not _in_us_bounds(lat, lon):
            raise GeocodingError(f"Coordinates {query!r} are outside the USA.")
        return lat, lon, f"{lat:.5f}, {lon:.5f}"

    cache_key = f"geocode:{query.strip().lower()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "us",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding service unavailable: {exc}") from exc

    if not results:
        raise GeocodingError(f"Could not find {query!r} in the USA.")

    hit = results[0]
    value = (float(hit["lat"]), float(hit["lon"]), hit["display_name"])
    cache.set(cache_key, value, CACHE_TTL)
    return value
