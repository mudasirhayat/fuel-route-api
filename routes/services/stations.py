"""In-memory fuel station catalogue with a coarse spatial grid index.

Stations are loaded once per process from data/stations.json (built offline by
scripts/build_station_coords.py), so finding stations near a route is pure
in-memory work with no external calls.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

from django.conf import settings

GRID_DEG = 0.25  # ~17 miles per cell side; candidates come from 3x3 blocks


class Station:
    __slots__ = ("id", "name", "address", "city", "state", "price", "lat", "lon")

    def __init__(self, raw):
        for field in self.__slots__:
            setattr(self, field, raw[field])

    def as_dict(self):
        return {field: getattr(self, field) for field in self.__slots__}


def _cell(lat, lon):
    return (int(lat // GRID_DEG), int(lon // GRID_DEG))


@lru_cache(maxsize=1)
def _catalogue():
    path = Path(settings.BASE_DIR) / "data" / "stations.json"
    stations = [Station(raw) for raw in json.loads(path.read_text())]
    grid = {}
    for station in stations:
        grid.setdefault(_cell(station.lat, station.lon), []).append(station)
    return stations, grid


def all_stations():
    return _catalogue()[0]


def stations_near(lat, lon):
    """Stations in the 3x3 grid block around a point (covers ~17mi radius)."""
    grid = _catalogue()[1]
    row, col = _cell(lat, lon)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            yield from grid.get((row + dr, col + dc), ())


def haversine_miles(lat1, lon1, lat2, lon2):
    rad = math.radians
    dlat, dlon = rad(lat2 - lat1), rad(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 3958.8 * 2 * math.asin(math.sqrt(a))
