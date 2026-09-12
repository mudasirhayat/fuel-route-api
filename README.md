# Fuel Route API

Django API that plans a driving route between two locations in the USA and
picks the **cost-optimal fuel stops** along the way, using a catalogue of
~6,300 real truck stop fuel prices.

- Vehicle range: **500 miles** per full tank
- Fuel economy: **10 mpg**
- Routing and map data: **OpenStreetMap** (OSRM for directions, Nominatim for
  geocoding, Leaflet for the map view) — all free, no API keys required
- **At most 3 external calls per request** (2 geocodes + 1 route), and all of
  them are cached, so a repeated request makes **zero** external calls and
  returns in ~100 ms

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Then:

```
GET http://127.0.0.1:8000/api/route/?start=New York, NY&finish=Los Angeles, CA
```

Locations can be free text (`Denver, CO`) or raw coordinates (`39.74,-104.99`).
POST with a JSON body `{"start": "...", "finish": "..."}` works too.

## Example response (abridged)

```json
{
  "start":  {"query": "New York, NY", "resolved": "New York, United States", "lat": 40.71, "lon": -74.0},
  "finish": {"query": "Los Angeles, CA", "resolved": "Los Angeles, California, United States", "lat": 34.05, "lon": -118.24},
  "route": {
    "distance_miles": 2794.0,
    "duration_hours": 49.8,
    "geometry_polyline": "...encoded polyline...",
    "geometry_geojson": {"type": "LineString", "coordinates": [...]}
  },
  "fuel_plan": {
    "total_fuel_cost_usd": 693.28,
    "total_gallons_purchased": 229.4,
    "trip_fuel_burned_gallons": 279.4,
    "stop_count": 17,
    "stops": [
      {
        "name": "SHEETZ #639", "city": "Youngstown", "state": "OH",
        "route_mile": 390.9, "detour_miles": 3.7,
        "price_per_gallon": 3.059, "gallons_purchased": 5.54, "fuel_cost_usd": 16.96
      }
    ]
  },
  "assumptions": {"vehicle_range_miles": 500, "vehicle_mpg": 10, "tank_at_start": "full"},
  "map_url": "http://127.0.0.1:8000/map/<token>/"
}
```

`map_url` opens an interactive Leaflet map showing the route, start/finish,
and every fuel stop with its purchase details.

## How it works

### 1. One routing call, cached

`start` and `finish` are geocoded with Nominatim (cached for 24 h, skipped
entirely for `lat,lon` inputs), then a **single** OSRM request returns the
full route geometry and distance (cached for 6 h).

### 2. Station matching with zero external calls

The assessment CSV has no coordinates, so geocoding stations at request time
would need thousands of API calls. Instead, `scripts/build_station_coords.py`
matches every station's city/state against the GeoNames US places dump
**once, offline** (99.7% match rate) and the result is committed as
`data/stations.json`. At runtime the catalogue is loaded once per process
into a spatial grid index; finding all stations within 10 miles of a
2,800-mile route takes a few tens of milliseconds of pure in-memory work.

### 3. Provably cost-optimal stop selection

Stop selection is the classic **gas station problem**, solved with the greedy
strategy that is provably optimal for a fixed tank range:

> At each station, if a *cheaper* station is reachable on a full tank, buy
> just enough fuel to get there. Otherwise fill up and drive to the cheapest
> reachable station.

The trip start is modelled as a free full tank and the destination as a
price-zero node, so the first and last legs fall out of the same rule. This
is why the plan sometimes includes small "bridge" purchases of a few gallons
at a pricier station: that is exactly what minimises total cost. If a
stretch of route longer than 500 miles has no stations, the API returns a
clear 422 error instead of an impossible plan.

## Assumptions

- The tank is full at the start; the reported cost is the fuel purchased en
  route to complete the trip (a trip under 500 miles therefore costs $0 and
  `trip_fuel_burned_gallons` is reported separately for reference).
- Stations more than 10 miles from the route are ignored
  (`FUEL_MAX_DETOUR_MILES` in settings).
- Canadian rows in the source CSV (620 of 8,151) are excluded; the brief
  limits routes to the USA.

## Project layout

```
routes/services/geocoding.py   Nominatim client (cache + lat,lon fast path)
routes/services/routing.py     OSRM client (single call, cached)
routes/services/stations.py    station catalogue + spatial grid index
routes/services/planner.py     candidate matching + optimal stop selection
routes/views.py                API endpoint, map view
scripts/build_station_coords.py  offline one-time station geocoding
data/stations.json             committed output of the script above
```

## Tests

```bash
python manage.py test
```

12 tests cover the planner (optimality edge cases, infeasible gaps), the API
contract, coordinate fast-path behaviour, and the station catalogue.
