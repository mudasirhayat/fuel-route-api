import uuid

import polyline as polyline_codec
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.geocoding import GeocodingError, geocode
from .services.planner import PlanningError, find_candidates, plan_stops
from .services.routing import RoutingError, get_route

MAP_CACHE_TTL = 60 * 60 * 6


def _downsample(points, target=400):
    step = max(1, len(points) // target)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


class RouteFuelView(APIView):
    """Plan a route between two US locations with cost-optimal fuel stops.

    GET  /api/route/?start=<location>&finish=<location>
    POST /api/route/   {"start": "...", "finish": "..."}

    Locations can be free text ("Denver, CO") or "lat,lon" coordinates.
    """

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, params):
        start_q = (params.get("start") or "").strip()
        finish_q = (params.get("finish") or "").strip()
        if not start_q or not finish_q:
            return Response(
                {"error": "Both 'start' and 'finish' are required, e.g. "
                          "?start=New York, NY&finish=Los Angeles, CA"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            start_lat, start_lon, start_name = geocode(start_q)
            finish_lat, finish_lon, finish_name = geocode(finish_q)
            points, total_miles, duration_s = get_route(
                (start_lat, start_lon), (finish_lat, finish_lon)
            )
        except GeocodingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except RoutingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        candidates = find_candidates(points, total_miles)
        try:
            stops, totals = plan_stops(candidates, total_miles)
        except PlanningError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        map_points = _downsample(points)
        map_token = uuid.uuid4().hex
        map_payload = {
            "route": [[lat, lon] for lat, lon in map_points],
            "start": {"lat": start_lat, "lon": start_lon, "label": start_name},
            "finish": {"lat": finish_lat, "lon": finish_lon, "label": finish_name},
            "stops": stops,
            "summary": {
                "distance_miles": round(total_miles, 1),
                **totals,
            },
        }
        cache.set(f"map:{map_token}", map_payload, MAP_CACHE_TTL)

        return Response(
            {
                "start": {"query": start_q, "resolved": start_name,
                          "lat": start_lat, "lon": start_lon},
                "finish": {"query": finish_q, "resolved": finish_name,
                           "lat": finish_lat, "lon": finish_lon},
                "route": {
                    "distance_miles": round(total_miles, 1),
                    "duration_hours": round(duration_s / 3600, 1),
                    "geometry_polyline": polyline_codec.encode(map_points),
                    "geometry_geojson": {
                        "type": "LineString",
                        "coordinates": [[lon, lat] for lat, lon in map_points],
                    },
                },
                "fuel_plan": {**totals, "stops": stops},
                "assumptions": {
                    "vehicle_range_miles": 500,
                    "vehicle_mpg": 10,
                    "tank_at_start": "full",
                    "cost_basis": "fuel purchased en route to complete the trip",
                },
                "map_url": request.build_absolute_uri(f"/map/{map_token}/"),
            }
        )


def map_view(request, token):
    payload = cache.get(f"map:{token}")
    if payload is None:
        raise Http404("This map link has expired; request the route again.")
    return render(request, "routes/map.html", {"payload": payload})


def index(request):
    return JsonResponse(
        {
            "service": "Fuel Route API",
            "endpoints": {
                "plan": "/api/route/?start=<location>&finish=<location>",
                "map": "returned as map_url in each plan response",
            },
            "example": "/api/route/?start=New York, NY&finish=Los Angeles, CA",
        }
    )
