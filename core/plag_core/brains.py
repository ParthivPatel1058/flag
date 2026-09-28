"""The brain team: every AI PLAG has (Gemini, GLM 5.3 Flash, Muse, gpt-oss, mistral-nemotron, Groq) answers the same
question at once, and PLAG takes the best one in time.

Two ways to race:
- fast (commands, quick answers): the first good answer wins.
- deep (plans, reports, writing, hard questions): GLM and Muse also answer *thinking first* (they reason before they
  answer). When a quick answer arrives first, PLAG waits a few more seconds for a thinker; if one finishes in that
  window its answer wins, otherwise the quick one is used. Deep answers are better; the grace window caps the wait.
"""

import asyncio
import time
from typing import Callable

from .config import MODELS
from .gemini import ProviderError, gemini
from .groq import groq
from .nvidia import MODEL_KEYS, nvidia

THINKERS = set(MODEL_KEYS)  # GLM 5.3 Flash and Muse


async def race(system: str, schema: dict, text: str, *, history: list[tuple[str, str]] | None = None,
               deep: bool = False, accept: Callable[[dict], bool] = lambda o: bool(o), timeout: float = 25.0,
               grace: float = 8.0, max_tokens: int = 700, route: str = "turn",
               gemini_models: list[str] | None = None) -> tuple[str, dict]:
    """(model, answer) from the brain team. `accept` rejects answers that are plainly unusable."""
    history = history or []
    jobs: dict[asyncio.Task, str] = {}
    jobs[asyncio.create_task(gemini.turn(system=system, schema=schema, history=history, text=text,
                                         models=gemini_models or MODELS["turn"], route=route))] = "fast"
    for m in nvidia.models():
        think = deep and m in THINKERS
        jobs[asyncio.create_task(nvidia.turn(system=system, history=history, text=text, schema=schema, model=m,
                                             max_tokens=max_tokens, think=think,
                                             timeout=(timeout + grace) if think else None))] = "deep" if think else "fast"
    if groq.ready():
        jobs[asyncio.create_task(groq.turn(system=system, history=history, text=text, schema=schema))] = "fast"
    has_thinker = "deep" in jobs.values()
    end = time.monotonic() + timeout + (grace if has_thinker else 0)
    best: tuple[str, dict] | None = None
    grace_end: float | None = None
    errors: list[Exception] = []
    pending = set(jobs)
    try:
        while pending:
            limit = min(end, grace_end) if grace_end else end
            left = limit - time.monotonic()
            if left <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                try:
                    model, obj, _ms = t.result()
                except ProviderError as e:
                    errors.append(e)
                    continue
                except Exception as e:  # a racer crashing never ends the race
                    errors.append(e)
                    continue
                if not isinstance(obj, dict) or not accept(obj):
                    continue
                if jobs[t] == "deep" or not (deep and has_thinker):
                    return model, obj  # a thinker's answer, or a fast race: take it
                if best is None:
                    best = (model, obj)
                    grace_end = time.monotonic() + grace  # a quick answer is in hand: give the thinkers a moment
            if best and not any(jobs[t] == "deep" for t in pending):
                break  # every thinker has failed: no reason to wait
    finally:
        for t in jobs:
            t.cancel()
    if best:
        return best
    raise errors[0] if errors and isinstance(errors[0], ProviderError) else ProviderError("No AI answered in time", "timeout")
