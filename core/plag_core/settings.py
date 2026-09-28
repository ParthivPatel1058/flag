"""Your PLAG settings (non-secret): voice provider choices, how patient listening is, wake sensitivity.
Stored in %LOCALAPPDATA%\\PLAG\\settings.json. API keys never go here; they live in Windows Credential Manager."""

import json
import re
import threading

from .config import DATA_DIR

PATH = DATA_DIR / "settings.json"
DEFAULTS = {
    "eleven_voice_id": "",                   # chosen in the dashboard; empty = the first voice on your account
    "eleven_model": "eleven_flash_v2_5",     # fast and half the credits; or "eleven_v3_conversational" (expressive)
    "eleven_speak": True,                    # use ElevenLabs for PLAG's voice when a key is saved
    "eleven_hear": True,                     # use ElevenLabs Scribe for commands that aren't simple
    "eleven_realtime": True,                 # hear you word by word while you speak (Scribe v2 Realtime)
    "eleven_max_chars": 1000,                # every reply in your ElevenLabs voice (lower it to save credits)
    "eleven_reserve_pct": 10,                # keep this much of the monthly quota: below it, the local voice takes over
    "speed": 1.0,
    "turn": "normal",                        # fast | normal | patient: how long a pause ends what you're saying
    "wake_sensitivity": "normal",            # low | normal | high
    # after PLAG answers, keep listening without "PLAG": off | questions (only when PLAG asks you something) |
    # always (also a few seconds after a spoken answer; never after opening or playing something)
    "follow_up": "always",
    "home_city": "",                         # for "what's the weather"; set the first time you name a city
    "use_location": True,                    # Windows Location: "where am I", and the weather where you are
    "always_listen": False,                  # the ear button: every phrase is for PLAG (off: only after "PLAG")
    "voice_agent": True,                     # talk through your ElevenLabs agent; PLAG's own voice is the backup
    "barge_in": True,                        # talk over PLAG to interrupt it: it stops and listens, like a person
    "eleven_agent_id": "",                   # empty: the agent named "PLAG" on your ElevenLabs account
    # PLAG's voice: auto (Sarvam when its key is saved, else Leo on NVIDIA, else Edge, else ElevenLabs, else offline)
    # or one of them first; the others stay as backups
    "voice_engine": "auto",
    "sarvam_speaker": "shubh",               # Sarvam Bulbul v3 voice
    "edge_voice": "hi-IN-MadhurNeural",      # Microsoft Edge voice (free, no key)
    "fish_voice_id": "",                     # Fish Audio voice model id; empty = the most used "Jarvis" voice, found once
    # Inbox agent (inbox.py): new messages from the accounts you connected, each with a reply PLAG drafts (never sends)
    "inbox_agent": True,                     # watch connected accounts and Gmail for new messages
    "inbox_draft": True,                     # draft a reply for each (the message goes to the AI to write it)
    "inbox_announce": True,                  # say new messages out loud (always shown in the Inbox tab)
    # Computer use (deskagent.py): PLAG clicking and typing in your apps to finish a job you asked for
    "computer_use": False,                   # off until you turn it on
    "computer_apps": "",                     # apps it may drive, comma-separated ("notepad.exe, winword.exe"); empty = any app but the blocked ones
    "inbox_owner": "",                       # your name, so drafts are signed and written as you
    "v": 2,                                  # settings version, for moving old defaults forward once
}
VOICE_ENGINES = ("auto", "fish", "sarvam", "nvidia", "edge", "elevenlabs", "local")
# Sarvam Bulbul v3 voices (docs.sarvam.ai, 2026-09-25), men first: the rest are women
SARVAM_SPEAKERS = ("shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun", "manan", "sumit", "kabir", "aayan",
                   "ashutosh", "advait", "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham",
                   "ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "shreya", "roopa", "tanya", "shruti",
                   "suhani", "kavitha", "rupali")
EDGE_VOICES = ("hi-IN-MadhurNeural", "en-IN-PrabhatNeural", "hi-IN-SwaraNeural", "en-IN-NeerjaNeural",
               "en-IN-NeerjaExpressiveNeural")
CHOICES = {"eleven_model": {"eleven_flash_v2_5", "eleven_v3_conversational", "eleven_multilingual_v2"},
           "turn": {"fast", "normal", "patient"}, "wake_sensitivity": {"low", "normal", "high"},
           "follow_up": {"off", "questions", "always"}, "voice_engine": set(VOICE_ENGINES),
           "sarvam_speaker": set(SARVAM_SPEAKERS), "edge_voice": set(EDGE_VOICES)}
PAUSE_S = {"fast": 0.7, "normal": 1.0, "patient": 1.3}  # silence that ends a phrase
_lock = threading.Lock()
_cache: dict | None = None


def get() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                saved = json.loads(PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            if saved and saved.get("v", 1) < 2 and saved.get("eleven_max_chars") == 220:
                saved["eleven_max_chars"] = 1000  # v2: every reply in ElevenLabs (220 was the old default, not a choice)
            saved["v"] = DEFAULTS["v"]
            _cache = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
        return dict(_cache)


def update(changes: dict) -> dict:
    """Apply the valid changes, save, and return the full settings."""
    global _cache
    current = get()
    for key, value in changes.items():
        if key not in DEFAULTS or key == "v":
            continue
        if key == "speed" and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = min(1.2, max(0.8, float(value)))
        elif type(value) is not type(DEFAULTS[key]):
            continue
        if key in CHOICES and value not in CHOICES[key]:
            continue
        if key == "eleven_max_chars":
            value = min(1000, max(40, value))
        if key == "eleven_reserve_pct":
            value = min(50, max(0, value))
        if key == "fish_voice_id":
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", value):
                continue  # a voice id is letters and digits (from the voice page's address)
        if key == "computer_apps":
            value = ", ".join(sorted({a.strip().lower()[:40] for a in value.split(",") if a.strip()}))[:400]
        if key in ("home_city", "inbox_owner"):
            value = " ".join(value.split())[:60]
        current[key] = value
    with _lock:
        PATH.write_text(json.dumps(current, indent=1), encoding="utf-8")
        _cache = current
    return dict(current)
