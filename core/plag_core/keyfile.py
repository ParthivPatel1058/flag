"""Your keys, loaded once from a file on this laptop, so you never type one into Settings again.

Why a file and not the source code: everything in the source goes to GitHub, and a key pushed to GitHub is public
forever, even in a private repo (it stays in the history, and GitHub's own scanners and every fork can read it).
So PLAG reads them from a file that never leaves this laptop and is ignored by git:

    %LOCALAPPDATA%\\PLAG\\keys.json        <- the normal place (made by setup-keys.ps1)
    <repo>\\keys.local.json               <- handy while developing

Either is plain JSON, short names or full ones, both work:

    {"gemini": "...", "groq": "gsk_...", "nvidia_glm_api_key": "nvapi-..."}

At every start PLAG copies what it finds into Windows Credential Manager (where the rest of PLAG looks) and then
forgets the values. Keys already saved are left alone unless the file says {"overwrite": true}.
"""

import json
import logging
import os
import sys
from pathlib import Path

from .secrets import get_secret, set_secret

log = logging.getLogger("plag.keys")

FILENAME = "keys.json"
# Short name -> the name the rest of PLAG looks up. Writing "gemini" in the file is enough.
ALIASES = {
    "gemini": "gemini_api_key",
    "google_ai": "gemini_api_key",
    "groq": "groq_api_key",
    "tavily": "tavily_api_key",
    "tinyfish": "tinyfish_api_key",
    "calcom": "calcom_api_key",
    "cal": "calcom_api_key",
    "elevenlabs": "elevenlabs_api_key",
    "eleven": "elevenlabs_api_key",
    "fishaudio": "fishaudio_api_key",
    "fish": "fishaudio_api_key",
    "sarvam": "sarvam_api_key",
    "olamaps": "olamaps_api_key",
    "ola": "olamaps_api_key",
    "nvidia": "nvidia_api_key",
    "nvidia_llm": "nvidia_llm_api_key",
    "nvidia_llm_2": "nvidia_llm_api_key_2",
    "nvidia_muse": "nvidia_muse_api_key",
    "muse": "nvidia_muse_api_key",
    "nvidia_glm": "nvidia_glm_api_key",
    "glm": "nvidia_glm_api_key",
    "nvidia_speech": "nvidia_speech_api_key",
    "speech": "nvidia_speech_api_key",
    "nvidia_hearing": "nvidia_hearing_api_key",
    "hearing": "nvidia_hearing_api_key",
    "nvidia_image": "nvidia_image_api_key",
    "nvidia_trellis": "nvidia_trellis_api_key",
    "nvidia_weather": "nvidia_weather_api_key",
}
# Anything else ending in _api_key (or the two Google OAuth values) is taken as it is.
PASS_THROUGH = {"google_client", "google_refresh_token"}
SETTINGS = {"overwrite"}


def _places() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("PLAG_KEYS_FILE")
    if env:
        out.append(Path(env))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "PLAG" / FILENAME)
    else:  # so it can be tested off Windows
        out.append(Path.home() / ".config" / "plag" / FILENAME)
    out.append(Path(__file__).resolve().parents[2] / "keys.local.json")
    return out


def path() -> Path:
    """Where setup-keys.ps1 should write the file: %LOCALAPPDATA%\\PLAG\\keys.json, or PLAG_KEYS_FILE if it's set."""
    return _places()[0]


def _name(key: str) -> str | None:
    k = key.strip().casefold()
    if k in SETTINGS:
        return None
    if k in ALIASES:
        return ALIASES[k]
    if k in PASS_THROUGH or k.endswith("_api_key"):
        return k
    return None


def _read(p: Path) -> dict:
    try:
        raw = p.read_text("utf-8-sig")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as e:
        log.warning("%s isn't valid JSON (%s): no keys loaded from it", p.name, e)
        return {}
    return data if isinstance(data, dict) else {}


def load() -> list[str]:
    """Copy the keys from the file into Credential Manager. Returns the names saved (never the values)."""
    saved: list[str] = []
    for p in _places():
        if not p.is_file():
            continue
        data = _read(p)
        if not data:
            continue
        overwrite = bool(data.get("overwrite"))
        for raw_key, raw_value in data.items():
            name = _name(str(raw_key))
            if not name or not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            # placeholders from the example file, and anything obviously not a key, are skipped quietly
            if len(value) < 10 or value.startswith(("<", "your", "YOUR", "paste", "PASTE")):
                continue
            if not overwrite and get_secret(name):
                continue
            try:
                set_secret(name, value)
            except Exception as e:  # Credential Manager unavailable: PLAG still runs on whatever is already saved
                log.warning("couldn't save %s: %s", name, e)
                continue
            saved.append(name)
        if saved:
            log.info("loaded %d key(s) from %s", len(saved), p.name)
        break  # the first file that has keys wins
    return saved


def status() -> dict:
    """For Settings: where the file is and whether it was found. Never any key."""
    for p in _places():
        if p.is_file():
            return {"found": True, "path": str(p), "keys": sorted({n for k in _read(p) if (n := _name(str(k)))})}
    return {"found": False, "path": str(path()), "keys": []}


if __name__ == "__main__":  # python -m plag_core.keyfile  ->  say what was loaded
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    names = load()
    print(f"loaded {len(names)} key(s): {', '.join(names)}" if names else "no new keys found")
    print(f"file: {status()['path']}")
    sys.exit(0)
