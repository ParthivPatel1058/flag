"""Directions like Google Maps, by voice: "take me to India Gate", "save this place as home", "ghar ka rasta batao".

Places and routes come from Ola Maps (India's own map data, live traffic), key in Windows Credential Manager as
PLAG / olamaps_api_key. Tested 2026-09-26: its place search (autocomplete) finds "India Gate", "Connaught Place" and
"Bangalore airport" right, while its plain geocode sent "India Gate, New Delhi" to a Chennai postcode, so only the
search is used. Without the key, or if Ola fails, the free OpenStreetMap services answer instead (Nominatim search,
OSRM routes: no traffic).

Saved places ("home", "office", "gym") live in %LOCALAPPDATA%\\PLAG\\places.json on this laptop only.
"""

import json
import math
import re
import uuid

import httpx

from .config import DATA_DIR
from .secrets import get_secret

OLA = "https://api.olamaps.io"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving"
AGENT = "PLAG-personal-assistant/0.2 (Windows desktop app)"
PLACES = DATA_DIR / "places.json"
_http = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))


class NavError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- saved places

def _key(label: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", label.casefold()).split())


def saved() -> dict[str, dict]:
    try:
        return json.loads(PLACES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_place(label: str, place: dict) -> dict:
    places = saved()
    entry = {"label": label.strip(), "name": place.get("name", ""), "address": place.get("address", ""),
             "lat": round(place["lat"], 6), "lng": round(place["lng"], 6)}
    places[_key(label)] = entry
    PLACES.write_text(json.dumps(places, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def forget_place(label: str) -> bool:
    places = saved()
    if places.pop(_key(label), None) is None:
        return False
    PLACES.write_text(json.dumps(places, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


# "home" / "ghar" / "my office" -> the saved label
_ALIASES = {"ghar": "home", "house": "home", "apna ghar": "home", "daftar": "office", "work": "office"}


def find_saved(said: str) -> dict | None:
    k = _key(re.sub(r"^(?:my|mera|meri|mere|apna|apne)\s+", "", said.strip(), flags=re.I))
    places = saved()
    return places.get(k) or places.get(_ALIASES.get(k, ""))


# ---------------------------------------------------------------- search

def _ola_key() -> str | None:
    return get_secret("olamaps_api_key")


def _headers() -> dict:
    return {"X-Request-Id": uuid.uuid4().hex}


async def search(query: str, near: dict | None = None) -> dict:
    """The place you named, preferring ones near you: {name, address, lat, lng}."""
    key = _ola_key()
    if key:
        params = {"input": query, "api_key": key}
        if near:
            params["location"] = f"{near['lat']},{near['lng']}"
        try:
            r = await _http.get(f"{OLA}/places/v1/autocomplete", params=params, headers=_headers())
            preds = (r.json().get("predictions") or []) if r.status_code == 200 else []
            if preds:
                p = preds[0]
                loc = (p.get("geometry") or {}).get("location") or {}
                fmt = p.get("structured_formatting") or {}
                if "lat" in loc:
                    return {"name": fmt.get("main_text") or p.get("description", query).split(",")[0],
                            "address": p.get("description", ""), "lat": loc["lat"], "lng": loc["lng"]}
        except (httpx.HTTPError, ValueError):
            pass
    try:  # OpenStreetMap: free, no key
        r = await _http.get(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in"},
                            headers={"User-Agent": AGENT, "Accept-Language": "en"})
        hits = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError) as e:
        raise NavError("I can't reach the maps right now. Check the internet.", "offline") from e
    if not hits:
        raise NavError(f"I couldn't find {query} on the map.", "not_found")
    h = hits[0]
    return {"name": h.get("name") or query, "address": h.get("display_name", ""), "lat": float(h["lat"]), "lng": float(h["lon"])}


# ---------------------------------------------------------------- routes

def decode_polyline(s: str) -> list[list[float]]:
    """Google's encoded polyline (what Ola and OSRM send) -> [[lat, lng], ...]."""
    points, i, lat, lng = [], 0, 0, 0
    while i < len(s):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        points.append([lat / 1e5, lng / 1e5])
    return points


def _turn(maneuver: str, text: str) -> str:
    """One of: depart, left, right, slight-left, slight-right, sharp-left, sharp-right, uturn, roundabout, straight,
    arrive (the dashboard draws the arrow)."""
    # the words you hear win over Ola's maneuver code (2026-09-26: "Turn left onto Pandit Pant Marg" came as "roundabout")
    return _turn_of(text) or _turn_of(maneuver.replace("-", " ")) or "straight"


def _turn_of(said: str) -> str:
    m = said.casefold()
    for word, kind in (("arrive", "arrive"), ("destination", "arrive"), ("depart", "depart"), ("head", "depart"),
                       ("roundabout", "roundabout"), ("rotary", "roundabout"), ("u-turn", "uturn"), ("uturn", "uturn"),
                       ("sharp left", "sharp-left"), ("sharp-left", "sharp-left"), ("sharp right", "sharp-right"),
                       ("sharp-right", "sharp-right"), ("slight left", "slight-left"), ("slight-left", "slight-left"),
                       ("keep left", "slight-left"), ("slight right", "slight-right"), ("slight-right", "slight-right"),
                       ("keep right", "slight-right"), ("left", "left"), ("right", "right")):
        if word in m:
            return kind
    return ""


async def _ola_route(o: dict, d: dict, key: str) -> dict | None:
    try:
        r = await _http.post(f"{OLA}/routing/v1/directions", headers=_headers(),
                             params={"origin": f"{o['lat']},{o['lng']}", "destination": f"{d['lat']},{d['lng']}", "api_key": key})
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    routes = data.get("routes") or []
    if not routes:
        return None
    leg = routes[0]["legs"][0]
    steps = [{"text": s.get("instructions", ""), "turn": _turn(s.get("maneuver", ""), s.get("instructions", "")),
              "distance_m": int(s.get("distance") or 0), "lat": s["start_location"]["lat"], "lng": s["start_location"]["lng"]}
             for s in leg.get("steps") or []]
    # 2026-09-26: Ola gave leg "distance": 3.85 (km) for a 3.9 km route, while its steps are in metres
    dist = float(leg.get("distance") or 0)
    dist_m = dist * 1000 if dist < sum(s["distance_m"] for s in steps) / 10 else dist
    return {"distance_m": int(dist_m), "duration_s": int(leg.get("duration") or 0) or sum(int(s.get("duration") or 0)
            for s in leg.get("steps") or []), "steps": steps,
            "path": decode_polyline(routes[0].get("overview_polyline") or ""), "traffic": True, "by": "Ola Maps"}


async def _osrm_route(o: dict, d: dict) -> dict:
    try:
        r = await _http.get(f"{OSRM}/{o['lng']},{o['lat']};{d['lng']},{d['lat']}",
                            params={"overview": "full", "steps": "true", "geometries": "polyline"}, headers={"User-Agent": AGENT})
        data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        raise NavError("I can't reach the maps right now. Check the internet.", "offline") from e
    if not data.get("routes"):
        raise NavError("I couldn't find a road route there.", "no_route")
    rt = data["routes"][0]
    steps = []
    for s in rt["legs"][0]["steps"]:
        man = s.get("maneuver") or {}
        mod = f"{man.get('type', '')} {man.get('modifier', '')}".strip()
        road = s.get("name") or ""
        text = {"depart": f"Head out{' on ' + road if road else ''}", "arrive": "You have arrived"}.get(
            man.get("type", ""), f"{mod.replace('turn ', '').capitalize()}{' onto ' + road if road else ''}")
        loc = man.get("location") or [0, 0]
        steps.append({"text": text, "turn": _turn(mod, text), "distance_m": int(s.get("distance") or 0), "lat": loc[1], "lng": loc[0]})
    return {"distance_m": int(rt["distance"]), "duration_s": int(rt["duration"]), "steps": steps,
            "path": decode_polyline(rt.get("geometry") or ""), "traffic": False, "by": "OpenStreetMap"}


async def route(origin: dict, dest: dict) -> dict:
    key = _ola_key()
    r = await _ola_route(origin, dest, key) if key else None
    return r or await _osrm_route(origin, dest)


# ---------------------------------------------------------------- following along

def metres(a: dict, b: dict) -> float:
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    dla, dln = la2 - la1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dln / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def next_step(steps: list[dict], here: dict) -> tuple[int, float]:
    """Which turn is next from where you are now, and how far it is: (index, metres)."""
    if not steps:
        return 0, 0.0
    nearest = min(range(len(steps)), key=lambda i: metres(here, steps[i]))
    i = min(nearest + 1, len(steps) - 1) if metres(here, steps[nearest]) < 40 else nearest
    return i, metres(here, steps[i])


def say_distance(m: float) -> str:
    if m >= 1000:
        return f"{m / 1000:.1f} km".replace(".0 km", " km")
    return f"{int(round(m / 50) * 50) or 50} m"


def say_duration(s: int) -> str:
    mins = max(1, round(s / 60))
    return f"{mins // 60} hr {mins % 60} min" if mins >= 60 else f"{mins} min"
