"""In-process event bus. Subscribers (dashboard WebSockets) each get a bounded queue."""

import asyncio
import itertools
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._seq = itertools.count(1)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subs)

    def publish(self, topic: str, data: dict[str, Any] | list | None = None, task_id: str | None = None) -> dict:
        evt = {"v": 1, "seq": next(self._seq), "at": time.time(), "topic": topic, "task_id": task_id, "data": data or {}}
        for q in list(self._subs):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # a stalled dashboard must never block the agent
        return evt


bus = EventBus()
