"""Paths, model routes and runtime settings."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PLAG"
LOG_DIR = DATA_DIR / "logs"
TTS_CACHE_DIR = DATA_DIR / "cache" / "tts"
for _d in (LOG_DIR, TTS_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Large local models live next to the project, not in AppData: when a packaged app (like the Claude desktop app)
# downloads into AppData, Windows redirects the files to that app's private folder and PLAG can't see them.
MODELS_DIR = Path(os.environ.get("PLAG_MODELS_DIR") or Path(__file__).resolve().parents[2] / "models")

# Model routes: first healthy model wins. IDs verified against the Gemini key on 2026-09-23.
MODELS = {
    # one call: audio or text in -> transcript + language + action + reply (JSON)
    # the free tier is often overloaded (503), so there are several fallbacks. Gemma was the backup brain until
    # 2026-09-28; Muse and GLM 5.3 Flash on NVIDIA (nvidia.py) now race Gemini instead.
    "turn": ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.7-flash", "gemini-flash-latest"],
    # speech out (supports Hindi). Free tier: 10 requests/day per model, so the dashboard prefers local voices.
    "tts": ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"],
    # camera: "what is this?"
    "vision": ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.7-flash", "gemini-flash-latest"],
}

# Fastest thinking level each model accepts (others reject "minimal" with a 400). Unlisted models get none.
THINKING = {
    "gemini-3.1-flash-lite": "minimal",
    "gemini-flash-lite-latest": "minimal",
    "gemini-3.7-flash": "low",
    "gemini-flash-latest": "low",
}

# Gemini prebuilt voice. PLAG speaks with masculine Hindi forms, so the default voice matches.
VOICE = os.environ.get("PLAG_VOICE", "Charon")

# Per-attempt time limit (seconds). Overloaded free-tier models can take 10+ s just to return a 503,
# so a slow attempt is abandoned and the next model in the route is tried.
ROUTE_TIMEOUT = {"turn": 14.0, "vision": 12.0, "tts": 25.0, "write": 50.0}  # write: a whole essay or report
# Total time a route may spend across all its fallbacks before giving up with a clear message.
ROUTE_BUDGET = {"turn": 16.0, "vision": 16.0, "tts": 30.0, "write": 75.0}

# Seconds a model is skipped after it returns 429/5xx or times out.
BREAKER_SECONDS = {"rate": 30, "server": 30, "timeout": 30}
