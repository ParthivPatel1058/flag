"""Pending approvals for L2 actions. Single-use, short-lived, bound to the exact action."""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field

TTL_SECONDS = 90


@dataclass
class Pending:
    id: str
    tool: str
    args: dict
    summary: dict                 # what the approval card shows (recipient, message...)
    lang: str
    action_hash: str
    expires_at: float = field(default_factory=lambda: time.time() + TTL_SECONDS)


_pending: dict[str, Pending] = {}


def _hash(tool: str, args: dict) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def create(tool: str, args: dict, summary: dict, lang: str) -> Pending:
    # only one pending approval at a time: a new request replaces the old one
    _pending.clear()
    p = Pending(uuid.uuid4().hex[:10], tool, args, summary, lang, _hash(tool, args))
    _pending[p.id] = p
    return p


def take(approval_id: str) -> Pending | None:
    """Remove and return the approval if it exists, is unexpired and still matches its action."""
    p = _pending.pop(approval_id, None)
    if p is None or p.expires_at < time.time() or p.action_hash != _hash(p.tool, p.args):
        return None
    return p


def clear() -> None:
    _pending.clear()
