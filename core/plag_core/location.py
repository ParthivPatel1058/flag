"""Where this laptop is: Windows Location (the same service the Weather and Maps apps use) and the street address
for it from OpenStreetMap.

Measured 2026-09-24 on this laptop: Windows answers in ~3-4 s with ~70 m accuracy (location is allowed in Windows
privacy settings); OpenStreetMap's reverse lookup takes ~1-3 s. Both are cached for 10 minutes, and the position is
kept in memory only, never written to disk or the audit log. Used for "where am I", "what's my address", and the
weather when you don't name a city. Settings -> "Use my location" turns it off.
"""

import asyncio
import subprocess
import time

import httpx

from . import settings

REVERSE = "https://nominatim.openstreetmap.org/reverse"
AGENT = "PLAG-personal-assistant/0.2 (Windows desktop app)"  # OpenStreetMap asks every app to name itself
FRESH_S = 600
# .NET's location watcher (built into Windows): start, wait up to 8 s for a fix, print "lat lon accuracy"
_PS = ("Add-Type -AssemblyName System.Device;"
       "$w=New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High);"
       "[void]$w.TryStart($false,[TimeSpan]::FromSeconds(8));$d=(Get-Date).AddSeconds(8);"
       "while($w.Status -ne 'Ready' -and (Get-Date) -lt $d){Start-Sleep -Milliseconds 150};"
       "$c=$w.Position.Location;"
       "if($w.Permission -ne 'Granted'){'denied'}elseif($c.IsUnknown){'unknown'}"
       "else{'{0} {1} {2}' -f $c.Latitude.ToString([cultureinfo]::InvariantCulture),"
       "$c.Longitude.ToString([cultureinfo]::InvariantCulture),[int]$c.HorizontalAccuracy};$w.Stop()")
_fix: dict | None = None
_place: dict | None = None
_lock = asyncio.Lock()
health: dict | None = None


class LocationError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def enabled() -> bool:
    return settings.get()["use_location"]


def _windows_fix() -> dict:
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS], capture_output=True,
                             text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LocationError("Windows didn't answer with a location.", "timeout") from e
    line = (out.stdout or "").strip().splitlines()[-1:] or [""]
    parts = line[0].split()
    if line[0] == "denied":
        raise LocationError("Location is off for apps. Turn it on in Windows Settings, Privacy and security, Location.", "denied")
    if len(parts) != 3:
        raise LocationError("Windows doesn't know where this laptop is right now.", "unknown")
    return {"lat": float(parts[0]), "lon": float(parts[1]), "accuracy_m": int(parts[2]), "at": time.time()}


async def position(fresh: bool = False) -> dict:
    """{lat, lon, accuracy_m, at} from Windows Location, cached for 10 minutes."""
    global _fix, health
    if not enabled():
        raise LocationError("Location is turned off in PLAG's settings.", "off")
    async with _lock:
        if _fix and not fresh and time.time() - _fix["at"] < FRESH_S:
            return _fix
        t0 = time.perf_counter()
        try:
            _fix = await asyncio.to_thread(_windows_fix)
        except LocationError as e:
            health = {"ok": False, "error": e.code}
            raise
        health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "accuracy_m": _fix["accuracy_m"]}
        return _fix


async def place(fresh: bool = False) -> dict:
    """Where you are, in words: {lat, lon, accuracy_m, road, area, city, district, state, postcode, country, address}."""
    global _place
    fix = await position(fresh)
    if _place and _place["at"] == fix["at"]:
        return _place
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as http:
            r = await http.get(REVERSE, params={"lat": fix["lat"], "lon": fix["lon"], "format": "jsonv2", "zoom": 18,
                                                "addressdetails": 1},
                               headers={"User-Agent": AGENT, "Accept-Language": "en"})
        a = (r.json() if r.status_code == 200 else {}).get("address") or {}
    except (httpx.HTTPError, ValueError):
        a = {}  # offline: the coordinates are still right
    road = a.get("road") or a.get("pedestrian") or ""
    area = a.get("neighbourhood") or a.get("suburb") or a.get("quarter") or a.get("city_district") or ""
    city = a.get("city") or a.get("town") or a.get("village") or a.get("county") or ""
    parts = [p for p in (a.get("house_number", ""), road, area, city, a.get("state", ""), a.get("postcode", ""),
                         a.get("country", "")) if p]
    _place = {**fix, "road": road, "area": area, "city": city, "district": a.get("state_district") or "",
              "state": a.get("state") or "", "postcode": a.get("postcode") or "", "country": a.get("country") or "",
              "address": ", ".join(dict.fromkeys(parts))}
    return _place


def known_city() -> str:
    """The city from the last lookup (no new lookup): for the AI's context, never blocks."""
    return (_place or {}).get("city", "") if enabled() else ""
