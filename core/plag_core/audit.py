"""Append-only, hash-chained audit log: every tool call and turn, tamper-evident."""

import hashlib
import json
import threading
import time

from .config import LOG_DIR

_PATH = LOG_DIR / "audit.jsonl"
_lock = threading.Lock()
_prev: str | None = None


def _last_hash() -> str:
    if not _PATH.exists():
        return "0" * 64
    with _PATH.open("rb") as f:
        try:
            f.seek(-8192, 2)
        except OSError:
            f.seek(0)
        lines = f.read().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)["hash"]
        except (ValueError, KeyError):
            continue
    return "0" * 64


def audit(event: str, **data) -> None:
    global _prev
    with _lock:
        if _prev is None:
            _prev = _last_hash()
        rec = {"at": round(time.time(), 3), "event": event, "data": data, "prev": _prev}
        digest = hashlib.sha256((_prev + json.dumps(rec, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        rec["hash"] = digest
        with _PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _prev = digest
