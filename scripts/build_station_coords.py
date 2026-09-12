"""Build data/stations.json: fuel stations from the assessment CSV enriched
with coordinates, matched offline against the GeoNames US places dump.

This is a one-time preprocessing step. Its output is committed to the repo so
the API never has to geocode 7,000+ stations at request time (or call any
external service for station data at all).

Usage:
    python scripts/build_station_coords.py [path/to/US.txt]

If US.txt is not present it is downloaded from
https://download.geonames.org/export/dump/US.zip (~90 MB, CC-BY licensed).
"""
import csv
import io
import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"
OUT_PATH = BASE_DIR / "data" / "stations.json"
GEONAMES_URL = "https://download.geonames.org/export/dump/US.zip"

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.upper()
    s = s.replace("SAINT ", "ST ").replace("ST. ", "ST ")
    s = s.replace("MOUNT ", "MT ").replace("MT. ", "MT ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def expand_directionals(s: str) -> str:
    return re.sub(
        r"^(N|S|E|W) ",
        lambda m: {"N": "NORTH ", "S": "SOUTH ", "E": "EAST ", "W": "WEST "}[m.group(1)],
        s,
    )


def load_geonames(path: Path):
    """Index GeoNames populated places: (state, name) -> (lat, lon), keeping
    the most populous match for ambiguous names. A secondary index with spaces
    stripped catches spelling variants like MC LEAN / MCLEAN."""
    index, nospace = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[6] != "P":  # populated places only
                continue
            state = p[10]
            if state not in US_STATES:
                continue
            lat, lon, pop = float(p[4]), float(p[5]), int(p[14] or 0)
            names = {p[1], p[2]} | (set(p[3].split(",")) if p[3] else set())
            for name in filter(None, names):
                n = norm(name)
                for idx, key in ((index, (state, n)), (nospace, (state, n.replace(" ", "")))):
                    cur = idx.get(key)
                    if cur is None or pop > cur[0]:
                        idx[key] = (pop, lat, lon)
    return index, nospace


def lookup(index, nospace, state, city):
    n = norm(city)
    for candidate in (n, expand_directionals(n)):
        hit = index.get((state, candidate))
        if hit:
            return hit
    return nospace.get((state, n.replace(" ", "")))


def main():
    geonames_txt = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "US.txt"
    if not geonames_txt.exists():
        print(f"Downloading {GEONAMES_URL} ...")
        data = urllib.request.urlopen(GEONAMES_URL).read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            geonames_txt.write_bytes(zf.read("US.txt"))

    print("Indexing GeoNames places ...")
    index, nospace = load_geonames(geonames_txt)

    stations, seen = [], {}
    dropped_foreign = dropped_unmatched = 0
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            state, city = row["State"].strip(), row["City"].strip()
            if state not in US_STATES:
                dropped_foreign += 1  # Canadian rows; assessment is US-only
                continue
            hit = lookup(index, nospace, state, city)
            if hit is None:
                dropped_unmatched += 1
                continue
            _, lat, lon = hit
            price = float(row["Retail Price"])
            key = (row["Address"].strip(), city, state)
            if key in seen:  # duplicate listing: keep the cheapest price
                prev = seen[key]
                prev["price"] = min(prev["price"], price)
                continue
            station = {
                "id": int(row["OPIS Truckstop ID"]),
                "name": row["Truckstop Name"].strip(),
                "address": row["Address"].strip(),
                "city": city,
                "state": state,
                "price": price,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
            }
            seen[key] = station
            stations.append(station)

    OUT_PATH.write_text(json.dumps(stations, indent=1))
    print(
        f"Wrote {len(stations)} stations to {OUT_PATH} "
        f"(skipped {dropped_foreign} non-US rows, {dropped_unmatched} unmatched)"
    )


if __name__ == "__main__":
    main()
