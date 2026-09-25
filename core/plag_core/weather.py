"""Weather, two ways:
- the forecast for a city (today / tomorrow): Open-Meteo, free and keyless, answers in about 2 s;
- NVIDIA Earth-2 FourCastNet: an AI simulation of the whole planet's weather, 6 hours per step, shown as maps.

FourCastNet on NVIDIA's hosted API starts from one of four built-in sample atmospheres, not today's live data, so
it shows how weather systems evolve; it can't say whether it will rain in your city tomorrow (that's the forecast).

Measured 2026-09-24 with the user's key: the climate.api.nvidia.com gateway runs the model but answers "Large asset
written" without the maps; invoking the same NVCF function directly returns a 302 to a zip of PNG maps
(<variable>_<hour>_<member>.png) in ~7-9 s. Sample inputs 0-3 all work. Variables that work: t2m (temperature),
w10m (wind speed), tcwv (moisture), msl (pressure), z500; u10m is rejected, and one variable per run is safest.

Key: Windows Credential Manager, PLAG / nvidia_weather_api_key.
"""

import io
import re
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import httpx

from .imagegen import _pictures
from .secrets import get_secret

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
NVCF = "https://api.nvcf.nvidia.com/v2/nvcf"
KNOWN_FUNCTION = "5bdd622c-14f1-4783-854c-d9d310e4fab0"  # "ai-fourcastnet", as listed for this key on 2026-09-24
OUT_DIR = _pictures() / "PLAG" / "Weather"
VARIABLES = {  # what the maps show, in the words PLAG says
    "t2m": ("temperature", "तापमान", "temperature"),
    "w10m": ("wind speed", "हवा की रफ़्तार", "hawa ki speed"),
    "tcwv": ("moisture in the air", "हवा में नमी", "hawa mein nami"),
    "msl": ("air pressure", "हवा का दबाव", "hawa ka dabaav"),
}
_WORDS = [(r"wind|hawa|हवा|breeze|toofan|storm", "w10m"), (r"rain|moist|humid|cloud|baarish|barish|बारिश|nami|नमी", "tcwv"),
          (r"pressure|dabaav|दबाव|cyclone", "msl"), (r"temp|heat|hot|cold|garmi|sardi|गर्मी|ठंड|तापमान", "t2m")]
# WMO weather codes -> (English, Hindi, Hinglish)
_CODES = [((0,), ("clear skies", "आसमान साफ़", "aasmaan saaf")),
          ((1,), ("mostly clear", "ज़्यादातर साफ़", "zyadatar saaf")),
          ((2,), ("partly cloudy", "हल्के बादल", "halke baadal")),
          ((3,), ("overcast", "बादल छाए", "baadal chhaye")),
          ((45, 48), ("foggy", "कोहरा", "kohra")),
          ((51, 53, 55, 56, 57), ("drizzle", "बूंदाबांदी", "boondabaandi")),
          ((61, 63, 66, 80, 81), ("rain", "बारिश", "baarish")),
          ((65, 67, 82), ("heavy rain", "तेज़ बारिश", "tez baarish")),
          ((71, 73, 75, 77, 85, 86), ("snow", "बर्फ़बारी", "barfbaari")),
          ((95, 96, 99), ("thunderstorms", "आंधी-तूफ़ान", "aandhi-toofan"))]
health: dict | None = None  # last simulation, for the Connections panel
_runs: dict[str, dict] = {}  # id -> {variable, frames: [(hour, Path)]}
_function: str | None = None


class WeatherError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _key() -> str | None:
    return get_secret("nvidia_weather_api_key")


def ready() -> bool:
    return bool(_key())


def variable_for(text: str) -> str:
    """ "simulate the wind" -> w10m; anything else -> temperature."""
    t = (text or "").casefold()
    return next((v for rx, v in _WORDS if re.search(rx, t)), "t2m")


def describe(code: int | None) -> tuple[str, str, str]:
    return next((words for codes, words in _CODES if code in codes), ("mixed weather", "मिला-जुला मौसम", "mila-jula mausam"))


# ------------------------------------------------------------------ forecast (Open-Meteo)

async def forecast(city: str = "", *, lat: float | None = None, lon: float | None = None, name: str = "") -> dict:
    """{place, now: {temp, feels, humidity, wind, code}, days: [{date, code, high, low, rain}]} for today and tomorrow,
    for a city by name, or for coordinates (where you are, from Windows Location)."""
    city = " ".join((city or "").split()).strip(" .,?")
    if not city and lat is None:
        raise WeatherError("Which city?", "no_city")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0)) as http:
            if lat is not None and lon is not None:
                p = {"name": name or "your location", "latitude": lat, "longitude": lon}
            else:
                g = await http.get(GEO, params={"name": city, "count": 1, "language": "en", "format": "json"})
                hits = (g.json() if g.status_code == 200 else {}).get("results") or []
                if not hits:
                    raise WeatherError(f"I couldn't find a place called {city}.", "not_found")
                p = hits[0]
            f = await http.get(FORECAST, params={
                "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 2,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
    except httpx.HTTPError as e:
        raise WeatherError("I can't reach the weather service right now. Check the internet connection.", "offline") from e
    if f.status_code != 200:
        raise WeatherError(f"The weather service returned an error ({f.status_code}).", str(f.status_code))
    data = f.json()
    cur, daily = data.get("current") or {}, data.get("daily") or {}
    days = [{"date": d, "code": daily["weather_code"][i], "high": round(daily["temperature_2m_max"][i]),
             "low": round(daily["temperature_2m_min"][i]), "rain": daily["precipitation_probability_max"][i]}
            for i, d in enumerate(daily.get("time") or [])]
    return {"place": p["name"], "region": p.get("admin1") or "", "country": p.get("country") or "",
            "now": {"temp": round(cur.get("temperature_2m", 0)), "feels": round(cur.get("apparent_temperature", 0)),
                    "humidity": cur.get("relative_humidity_2m"), "wind": round(cur.get("wind_speed_10m", 0)),
                    "code": cur.get("weather_code")},
            "days": days}


# ------------------------------------------------------------------ simulation (FourCastNet)

async def _function_id(http: httpx.AsyncClient, key: str) -> str:
    """The NVCF function behind FourCastNet, found once from the functions this key can call."""
    global _function
    if _function:
        return _function
    try:
        r = await http.get(f"{NVCF}/functions", headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            for f in r.json().get("functions", []):
                if f.get("name") == "ai-fourcastnet" and f.get("status") == "ACTIVE":
                    _function = f["id"]
                    return _function
    except (httpx.HTTPError, ValueError):
        pass
    return KNOWN_FUNCTION


def frame_of(run_id: str, index: int) -> Path | None:
    run = _runs.get(run_id)
    if not run or not 0 <= index < len(run["frames"]):
        return None
    p = run["frames"][index][1]
    return p if p.exists() else None


def folder_of(run_id: str) -> Path | None:
    run = _runs.get(run_id)
    return run["dir"] if run else None


async def simulate(variable: str = "t2m", hours: int = 48, start: int = 0) -> dict:
    """Run FourCastNet `hours` ahead (6 h per step) and save the maps; returns {id, variable, hours, frames, ms, dir}."""
    global health
    key = _key()
    if not key:
        raise WeatherError("The weather simulation key is missing. Save it in Windows Credential Manager as "
                           "PLAG / nvidia_weather_api_key.", "no_key")
    variable = variable if variable in VARIABLES else "t2m"
    steps = max(1, min(40, hours // 6))
    t0 = time.perf_counter()
    auth = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0)) as http:
            fid = await _function_id(http, key)
            r = await http.post(f"{NVCF}/pexec/functions/{fid}", headers={**auth, "NVCF-POLL-SECONDS": "5"},
                                json={"input_id": start, "variables": variable, "simulation_length": steps})
            while r.status_code == 202 and time.perf_counter() - t0 < 150:  # still running: NVIDIA asks us to poll
                r = await http.get(f"{NVCF}/pexec/status/{r.headers.get('nvcf-reqid', '')}",
                                   headers={**auth, "NVCF-POLL-SECONDS": "5"})
            if r.status_code == 302 and (where := r.headers.get("location")):
                z = await http.get(where)  # a short-lived download link to a zip of maps
                blob = z.content if z.status_code == 200 else b""
            else:
                blob = b""
    except httpx.TimeoutException as e:
        health = {"ok": False, "ms": int((time.perf_counter() - t0) * 1000), "error": "timeout"}
        raise WeatherError("NVIDIA's weather model took too long. Try again in a minute.", "timeout") from e
    except httpx.TransportError as e:
        health = {"ok": False, "ms": 0, "error": "offline"}
        raise WeatherError("Can't reach NVIDIA's weather model. Check the internet connection.", "offline") from e
    ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code == 202:
        health = {"ok": False, "ms": ms, "error": "timeout"}
        raise WeatherError("NVIDIA's weather model is still busy. Try again in a minute.", "timeout")
    if not blob:
        health = {"ok": False, "ms": ms, "error": str(r.status_code)}
        raise WeatherError({401: "NVIDIA rejected the weather key. Check PLAG / nvidia_weather_api_key.",
                            403: "This NVIDIA key isn't allowed to use FourCastNet.",
                            429: "Too many weather simulations right now. Try again in a minute."}.get(
            r.status_code, f"NVIDIA's weather model returned an error ({r.status_code}). Try again in a minute."), str(r.status_code))
    folder = OUT_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{variable}"
    frames: list[tuple[int, Path]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                m = re.fullmatch(rf"{variable}_(\d{{3}})_000\.png", name)  # only the maps we asked for, member 0
                if m:
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{variable}_{int(m[1]):03d}h.png"
                    path.write_bytes(zf.read(name))
                    frames.append((int(m[1]), path))
    except zipfile.BadZipFile as e:
        health = {"ok": False, "ms": ms, "error": "bad_output"}
        raise WeatherError("NVIDIA's weather model sent back something unreadable. Try again.", "bad_output") from e
    if not frames:
        health = {"ok": False, "ms": ms, "error": "empty"}
        raise WeatherError("NVIDIA's weather model finished without any maps. Try again.", "empty")
    frames.sort()
    run_id = uuid.uuid4().hex[:10]
    _runs[run_id] = {"variable": variable, "frames": frames, "dir": folder}
    health = {"ok": True, "ms": ms, "error": None}
    return {"id": run_id, "variable": variable, "hours": [h for h, _ in frames], "ms": ms, "dir": str(folder), "start": start}
