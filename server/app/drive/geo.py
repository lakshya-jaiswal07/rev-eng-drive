"""Offline geography for the in-car assistant: every Indian city and town with
1,000+ people (GeoNames, CC BY 4.0 - see data/build_places.py), looked up by
name, alternate name or a near-miss spelling, and routed between.

Routes are estimates from coordinates, not turn-by-turn directions: road
distance = great-circle distance x a road factor, time from city/highway
speeds, the "via" town is the largest place in the corridor near the middle,
and stops are placed near a real town at a sensible point along the way. That
is honest for city-to-city driving without a map service, and it is fully
deterministic - the same request always gives the same route.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("data") / "india_places.tsv"


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    state: str
    lat: float
    lon: float
    population: int

    @property
    def label(self) -> str:
        if self.place_id.startswith("state:") or not self.state or self.state == self.name:
            return self.name
        return f"{self.name}, {self.state}"

    def to_dict(self) -> dict[str, object]:
        return {"id": self.place_id, "name": self.name, "state": self.state, "label": self.label,
                "lat": self.lat, "lon": self.lon}


def normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    text = re.sub(r"\b(the|city of|town of)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class Gazetteer:
    """Name -> places index over the bundled dataset."""

    def __init__(self, path: Path = DATA) -> None:
        self.places: list[Place] = []
        self.by_name: dict[str, list[Place]] = {}
        self.states: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or line.startswith("geonameid\t"):
                continue
            gid, name, state, lat, lon, pop, alternates = (line.split("\t") + [""] * 7)[:7]
            place = Place(gid, name, state, float(lat), float(lon), int(pop or 0))
            self.places.append(place)
            if state:
                self.states.add(normalise(state))
            for key in {normalise(name), *(normalise(a) for a in alternates.split("|") if a)}:
                if key:
                    self.by_name.setdefault(key, []).append(place)
        for key in self.by_name:  # most populous first: "Aurangabad" means the big one
            self.by_name[key].sort(key=lambda p: -p.population)
        # A state name ("Goa", "Kerala") routes to that state's most populous place.
        biggest: dict[str, Place] = {}
        for place in self.places:  # the data file is sorted by population
            if place.state and place.state not in biggest:
                biggest[place.state] = place
        for state, main in biggest.items():
            key = normalise(state)
            if key and key not in self.by_name:
                self.by_name[key] = [Place(f"state:{key}", f"{state} ({main.name})", state, main.lat, main.lon, main.population)]
        self.max_words = max(len(k.split()) for k in self.by_name)

    def exact(self, phrase: str, state: str | None = None) -> Place | None:
        candidates = self.by_name.get(normalise(phrase), [])
        if state:
            in_state = [p for p in candidates if normalise(p.state) == normalise(state)]
            candidates = in_state or candidates
        return candidates[0] if candidates else None

    def fuzzy(self, phrase: str) -> Place | None:
        """A near-miss spelling ("jaipor", "coimbatur") - speech recognisers mangle names."""
        key = normalise(phrase)
        if len(key) < 5:
            return None
        match = difflib.get_close_matches(key, self.by_name.keys(), n=1, cutoff=0.8)
        return self.by_name[match[0]][0] if match else None

    def lookup(self, phrase: str, state: str | None = None) -> Place | None:
        return self.exact(phrase, state) or self.fuzzy(phrase)

    def by_id(self, place_id: str) -> Place | None:
        if not hasattr(self, "_by_id"):
            self._by_id = {p.place_id: p for places in self.by_name.values() for p in places}
        return self._by_id.get(place_id)


@lru_cache(maxsize=1)
def gazetteer() -> Gazetteer:
    return Gazetteer()


# -- routing ----------------------------------------------------------------

def km_between(a: Place, b: Place) -> float:
    return _haversine(a.lat, a.lon, b.lat, b.lon)


def _haversine(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


STOP_KINDS = ("fuel", "charging", "food", "snacks", "coffee", "drinks", "rest", "pharmacy", "atm", "shopping",
              "custom", "place")
STOP_LABEL = {"fuel": "fuel", "charging": "EV charging", "food": "a meal", "snacks": "a snack break", "coffee": "coffee",
              "drinks": "drinks", "rest": "a rest break", "pharmacy": "a pharmacy", "atm": "an ATM",
              "shopping": "shopping"}
STOP_MINUTES = {"fuel": 8, "charging": 30, "food": 30, "snacks": 12, "coffee": 12, "drinks": 8, "rest": 10,
                "pharmacy": 10, "atm": 6, "shopping": 20, "custom": 10, "place": 10}
STOP_AT_FRACTION = {"fuel": 0.5, "charging": 0.55, "food": 0.45, "snacks": 0.4, "coffee": 0.3, "drinks": 0.35,
                    "rest": 0.6, "pharmacy": 0.5, "atm": 0.5, "shopping": 0.5, "custom": 0.5}


@dataclass(frozen=True)
class StopSpec:
    """A stop as the runtime stores it ("snacks", "custom:medicines", "place@<id>", "food@<id>"), decoded."""
    kind: str             # a STOP_KINDS entry
    label: str            # how it is said: "a snack break", "medicines", "Lonavla"
    at: Place | None      # the town it must be in, if one was named

    @staticmethod
    def parse(spec: str) -> "StopSpec":
        base, _, place_id = spec.partition("@")
        at = gazetteer().by_id(place_id) if place_id else None
        if base == "place":
            return StopSpec("place", at.name if at else "a stop", at)
        if base.startswith("custom:"):
            return StopSpec("custom", base[len("custom:"):], at)
        return StopSpec(base, STOP_LABEL.get(base, base), at)


def describe_stop(spec: str) -> str:
    """A stop in a short phrase: "a snack break", "a stop at Lonavla", "a meal at Lonavla"."""
    s = StopSpec.parse(spec)
    if s.kind == "place":
        return f"a stop at {s.label}"
    what = s.label if s.kind != "custom" else f"a stop for {s.label}"
    return f"{what} at {s.at.name}" if s.at else what


@dataclass
class Route:
    origin: Place
    destination: Place
    distance_km: float
    eta_min: int
    via: str
    avoid_highways: bool
    stops: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": self.origin.to_dict(),
            "destination": self.destination.to_dict(),
            "distance_km": self.distance_km,
            "eta_min": self.eta_min,
            "via": self.via,
            "avoid_highways": self.avoid_highways,
            "stops": list(self.stops),
        }


def _point_along(a: Place, b: Place, fraction: float) -> tuple[float, float]:
    return a.lat + (b.lat - a.lat) * fraction, a.lon + (b.lon - a.lon) * fraction


def _town_near(lat: float, lon: float, radius_km: float, exclude: set[str]) -> Place | None:
    """The most populous town within radius_km of a point (the likeliest place to stop)."""
    best: Place | None = None
    for p in gazetteer().places:  # sorted by population, so the first hit is the biggest
        if p.place_id in exclude:
            continue
        if abs(p.lat - lat) > radius_km / 100 or abs(p.lon - lon) > radius_km / 100:
            continue
        if _haversine(lat, lon, p.lat, p.lon) <= radius_km:
            best = p
            break
    return best


def _road_km(straight: float) -> float:
    return straight * (1.35 if straight < 40 else 1.22)


def _progress(origin: Place, destination: Place, p: Place) -> float:
    """How far along origin -> destination a place lies (0 at the start, 1 at the end)."""
    dx, dy = destination.lon - origin.lon, destination.lat - origin.lat
    span = dx * dx + dy * dy
    if span == 0:
        return 0.5
    return ((p.lon - origin.lon) * dx + (p.lat - origin.lat) * dy) / span


def plan_route(origin: Place, destination: Place, stop_kinds: tuple[str, ...] = (), avoid_highways: bool = False) -> Route:
    """Plan a trip. ``stop_kinds`` are stop specs (see StopSpec); a spec repeated N times is N stops.

    Named towns ("via Nashik", "lunch at Lonavala") become waypoints the route
    passes through, in driving order. Other stops are placed near a real town
    at a typical point of the trip; several of the same kind are spread out.
    """
    specs = [StopSpec.parse(s) for s in stop_kinds]
    endpoints = {origin.place_id, destination.place_id}

    # The road goes through every named town, in the order they come along the way.
    waypoints: list[Place] = []
    for s in specs:
        if s.at is not None and s.at.place_id not in endpoints and s.at not in waypoints:
            waypoints.append(s.at)
    waypoints.sort(key=lambda p: _progress(origin, destination, p))
    path = [origin, *waypoints, destination]
    legs = [km_between(a, b) for a, b in zip(path, path[1:])]
    straight = sum(legs)
    distance = sum(_road_km(leg) for leg in legs)
    km_at = {origin.place_id: 0.0}
    run = 0.0
    for place, leg in zip(path[1:], legs):
        run += _road_km(leg)
        km_at[place.place_id] = run

    def point_at(fraction: float) -> tuple[float, float]:
        """A point a fraction of the way along the whole path."""
        target = fraction * straight
        for (a, b), leg in zip(zip(path, path[1:]), legs):
            if target <= leg or b is destination:
                t = 0.0 if leg == 0 else min(1.0, target / leg)
                return a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t
            target -= leg
        return destination.lat, destination.lon

    if distance < 35:
        speed, via = 24.0, ""
    else:
        speed = 42.0 if avoid_highways else 58.0
        if waypoints:
            via = waypoints[0].name
        else:
            mid = _town_near(*point_at(0.5), max(20.0, straight * 0.15), endpoints)
            via = mid.name if mid else ""

    # Where each stop without a named town goes. One stop sits at its usual point of
    # the trip (coffee early, a rest break later); several are spread evenly in that
    # same order, so two snack stops land a third and two-thirds of the way along
    # and no two stops pile up in the same town.
    floating = [i for i, s in enumerate(specs) if s.at is None]
    usual = {i: STOP_AT_FRACTION.get(specs[i].kind, 0.5) for i in floating}
    if len(floating) == 1:
        fractions = {floating[0]: usual[floating[0]] if distance >= 35 else 0.5}
    else:
        ranked = sorted(floating, key=lambda i: usual[i])  # stable: repeats keep their order
        fractions = {i: (rank + 1) / (len(ranked) + 1) for rank, i in enumerate(ranked)}

    stops: list[dict[str, object]] = []
    extra_min = 0
    for i, (spec, s) in enumerate(zip(stop_kinds, specs)):
        if s.at is not None:  # in the named town
            near, km = s.at, km_at.get(s.at.place_id, 0.0 if s.at.place_id == origin.place_id else distance)
        else:
            fraction = fractions[i]
            found = _town_near(*point_at(fraction), max(15.0, straight * 0.12), endpoints)
            near = found or (origin if fraction < 0.5 else destination)
            km = distance * fraction
        stops.append({"kind": s.kind, "label": s.label, "near": near.name, "km_from_start": round(km, 1),
                      "at_named_place": s.at is not None, "spec": spec,
                      "lat": near.lat, "lon": near.lon})  # for the route map
        extra_min += STOP_MINUTES.get(s.kind, 10)

    minutes = distance / speed * 60 + extra_min
    return Route(origin, destination, round(distance, 1), max(1, round(minutes)), via, avoid_highways, stops)


def describe_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutes"
    hours, mins = divmod(minutes, 60)
    hours_text = f"{hours} hour" + ("s" if hours != 1 else "")
    return hours_text if mins < 5 else f"{hours_text} {mins} minutes"
