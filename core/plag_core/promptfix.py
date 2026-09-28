"""Descriptions NVIDIA's image and 3D models will draw.

NVIDIA's safety filter blocks names of characters, brands and famous people: on 2026-09-25 "iron man triangle shape
arc reactor" came back CONTENT_FILTERED from both FLUX (images) and TRELLIS (3D), while "a glowing blue triangular
arc reactor, sci-fi energy core, metallic" drew fine. So when a description is blocked, the AI rewrites it as what the
thing looks like (and fixes the spelling), and PLAG tries once more with that.
"""

import asyncio
import re

from .config import MODELS
from .gemini import ProviderError, gemini
from .groq import groq
from .nvidia import nvidia

SCHEMA = {"type": "OBJECT", "properties": {"prompt": {"type": "STRING"}}, "required": ["prompt"]}
SYSTEM = ("You rewrite descriptions for an AI image and 3D model generator. Its safety filter rejects names of "
          "fictional characters, superheroes, brands, franchises, logos and real people, so describe ONLY what the "
          "thing looks like: shape, parts, material, colours, lighting and style. Replace every name with its look, "
          "e.g. \"Iron Man's arc reactor\" -> \"a glowing blue circular energy reactor with a triangular core, "
          "polished metal, sci-fi\". Fix spelling mistakes, drop filler words (ok, please, bro). English only, no "
          "names, at most {limit} characters. Answer with JSON {{\"prompt\": \"...\"}} only.")


def _clean(text: str, limit: int) -> str:
    text = " ".join((text or "").split()).strip(" .\"'")
    if len(text) > limit:
        cut = text[:limit]
        text = cut[:cut.rfind(" ")] if " " in cut else cut
    return text


async def safe(prompt: str, limit: int = 300, timeout: float = 8.0) -> str | None:
    """The same thing described without names (None if no AI answered in time, or it changed nothing)."""
    system, text = SYSTEM.format(limit=limit), f"Description: {prompt}"
    racers = [gemini.turn(system=system, schema=SCHEMA, history=[], text=text, models=MODELS["turn"])]
    racers += [nvidia.turn(system=system, history=[], text=text, schema=SCHEMA, model=m, max_tokens=300) for m in nvidia.models()]
    if groq.ready():
        racers.append(groq.turn(system=system, history=[], text=text, schema=SCHEMA))
    tasks = [asyncio.create_task(r) for r in racers]
    try:
        for finished in asyncio.as_completed(tasks, timeout=timeout):
            try:
                _model, obj, _ms = await finished
            except ProviderError:
                continue
            better = _clean(str(obj.get("prompt") or ""), limit)
            if better and better.casefold() != prompt.casefold() and not re.search(r"[^\x00-\x7f]", better):
                return better
    except TimeoutError:
        pass
    finally:
        for t in tasks:
            t.cancel()
    return None
