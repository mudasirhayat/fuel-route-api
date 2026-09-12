"""Cost-optimal fuel stop planning along a route.

Two stages:

1. Candidate matching: the route polyline is downsampled to ~1 mile spacing
   and bucketed into a spatial grid; every station within MAX_DETOUR_MILES of
   the route is tagged with its mile position along the route. Pure in-memory
   work against the preloaded station catalogue, no external calls.

2. Stop selection: the classic "gas station problem" greedy, which is provably
   cost-optimal for a fixed tank range: at each station, if a cheaper station
   is reachable on a full tank, buy just enough fuel to get there; otherwise
   fill the tank and drive to the cheapest reachable station. The trip start
   is modelled as a station with price 0 and a full tank, which makes the
   first decision follow the same rule (jump to the cheapest reachable stop).
"""
from bisect import bisect_right
from dataclasses import dataclass

from django.conf import settings

from .stations import haversine_miles, stations_near

GRID_DEG = 0.25


class PlanningError(Exception):
    pass


@dataclass
class Candidate:
    station: object
    mile: float          # position along the route
    detour_miles: float  # straight-line distance from the route


def _sample_route(points, total_miles, spacing_miles=1.0):
    """Downsample the polyline to ~spacing_miles, keeping cumulative mileage."""
    samples = [(points[0][0], points[0][1], 0.0)]
    travelled = 0.0
    last = points[0]
    for point in points[1:]:
        travelled += haversine_miles(last[0], last[1], point[0], point[1])
        last = point
        if travelled - samples[-1][2] >= spacing_miles:
            samples.append((point[0], point[1], travelled))
    if samples[-1][2] != travelled:
        samples.append((points[-1][0], points[-1][1], travelled))
    # The haversine chain length differs slightly from OSRM's road distance;
    # rescale mile markers so they line up with the reported total.
    scale = total_miles / travelled if travelled else 1.0
    return [(lat, lon, mile * scale) for lat, lon, mile in samples]


def find_candidates(points, total_miles):
    """All stations within MAX_DETOUR_MILES of the route, with mile markers."""
    samples = _sample_route(points, total_miles)

    sample_grid = {}
    for lat, lon, mile in samples:
        cell = (int(lat // GRID_DEG), int(lon // GRID_DEG))
        sample_grid.setdefault(cell, []).append((lat, lon, mile))

    # Candidate stations: anything in the grid neighbourhood of any sample.
    stations = {}
    for cell, cell_samples in sample_grid.items():
        lat, lon, _ = cell_samples[0]
        for station in stations_near(lat, lon):
            stations[station.id] = station

    max_detour = settings.FUEL_MAX_DETOUR_MILES
    candidates = []
    for station in stations.values():
        row, col = int(station.lat // GRID_DEG), int(station.lon // GRID_DEG)
        closest = None
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for lat, lon, mile in sample_grid.get((row + dr, col + dc), ()):
                    dist = haversine_miles(station.lat, station.lon, lat, lon)
                    if dist <= max_detour and (closest is None or dist < closest[0]):
                        closest = (dist, mile)
        if closest:
            candidates.append(Candidate(station, closest[1], closest[0]))

    # Sort by mile, then price, so among stations at the same mile marker the
    # cheapest is always considered first.
    candidates.sort(key=lambda c: (c.mile, c.station.price))
    return candidates


def plan_stops(candidates, total_miles):
    """Optimal greedy for the gas station problem.

    Returns (stops, totals). Raises PlanningError when a stretch of the route
    longer than the tank range has no candidate stations.
    """
    max_range = float(settings.FUEL_MAX_RANGE_MILES)
    mpg = settings.FUEL_VEHICLE_MPG

    if total_miles <= max_range:
        return [], _totals([], total_miles, mpg)

    # Nodes: real stations plus a virtual destination that is cheaper than
    # everything, so "head for the first cheaper reachable node" naturally
    # buys only what is needed on the final leg.
    DESTINATION = Candidate(None, total_miles, 0.0)
    nodes = candidates + [DESTINATION]
    miles = [n.mile for n in nodes]

    def price_of(node):
        return -1.0 if node is DESTINATION else node.station.price

    purchases = []  # (candidate, gallons)
    position, price_here, here = 0.0, 0.0, None  # virtual start, tank is full
    fuel_miles = max_range

    while position < total_miles:
        lo = bisect_right(miles, position)
        hi = bisect_right(miles, position + max_range)
        reachable = nodes[lo:hi]
        if not reachable:
            raise PlanningError(
                f"Cannot complete this route: no fuel stations found between "
                f"mile {position:.0f} and mile {position + max_range:.0f} "
                f"(tank range {max_range:.0f} mi)."
            )

        cheaper = next((n for n in reachable if price_of(n) < price_here), None)
        if cheaper is not None:
            # Buy only what is needed to reach the first cheaper node.
            need = cheaper.mile - position - fuel_miles
            if need > 0:
                purchases.append((here, need / mpg))
                fuel_miles += need
            target = cheaper
        else:
            # Fill the tank here (free at the virtual start, where it is
            # already full) and drive to the cheapest reachable station.
            refill = max_range - fuel_miles
            if refill > 0 and here is not None:
                purchases.append((here, refill / mpg))
            fuel_miles = max_range
            target = min(reachable, key=price_of)

        if target.mile <= position + 1e-9 or target.mile - position > fuel_miles + 1e-9:
            raise PlanningError("Internal planning error: unreachable target stop.")

        fuel_miles -= target.mile - position
        position, price_here, here = target.mile, price_of(target), target

    stops = [
        {
            **candidate.station.as_dict(),
            "route_mile": round(candidate.mile, 1),
            "detour_miles": round(candidate.detour_miles, 1),
            "price_per_gallon": round(candidate.station.price, 3),
            "gallons_purchased": round(gallons, 2),
            "fuel_cost_usd": round(gallons * candidate.station.price, 2),
        }
        for candidate, gallons in purchases
    ]
    return stops, _totals(stops, total_miles, mpg)


def _totals(stops, total_miles, mpg):
    return {
        "total_fuel_cost_usd": round(sum(s["fuel_cost_usd"] for s in stops), 2),
        "total_gallons_purchased": round(sum(s["gallons_purchased"] for s in stops), 2),
        "trip_fuel_burned_gallons": round(total_miles / mpg, 2),
        "stop_count": len(stops),
    }
