"""Open models on NVIDIA NIM (OpenAI-compatible API), raced against Gemini: the first good answer wins.

Measured 2026-09-24 with the user's keys (PLAG-style commands, 2 rounds, JSON checked for the right action):
  openai/gpt-oss-20b            6/6 right, median 1.5 s     (reasoning_effort low)
  mistralai/mistral-nemotron    6/6 right, median 2.8 s
  meta/muse-glimmer-30b         always reasons first: 14-60 s a reply, too slow to speak
  z-ai/glm-5.3-flash, kimi-k3, gemma-4-31b, deepseek-v4.1-flash, nemotron-3.5-lightning: nothing in 30 s
With PLAG's full prompt (10k characters) both swung between ~1.5 and 11 s, so PLAG races both. A model that fails
twice in a row rests for five minutes.

2026-09-28: Muse (Meta) and GLM 5.3 Flash replace Gemma as PLAG's general-purpose brains (commands, answers, news
briefs, writing), each with its own key. Both are asked to answer without a long reasoning pass first (the reason
they were too slow on 2026-09-24); if one is still slow, the race simply goes to whoever answers first.

Keys: Windows Credential Manager. Each model's own key first (MODEL_KEYS), then PLAG / nvidia_llm_api_key,
nvidia_llm_api_key_2 and nvidia_api_key. A key that's refused or rate-limited hands the request to the next one.
"""

import json
import re
import time

import httpx

from .gemini import ProviderError
from .secrets import get_secret

URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MUSE = "meta/muse-glimmer-30b"
GLM = "z-ai/glm-5.3-flash"
MODELS = [GLM, MUSE, "openai/gpt-oss-20b", "mistralai/mistral-nemotron"]
MODEL_KEYS = {MUSE: "nvidia_muse_api_key", GLM: "nvidia_glm_api_key"}
SHARED_KEYS = ["nvidia_llm_api_key", "nvidia_llm_api_key_2", "nvidia_api_key"]
KEYS = [*MODEL_KEYS.values(), *SHARED_KEYS]
_COMMAND_KEYS = "transcript, language, actions, reply, mood"
_NO_THINKING = {"thinking": False, "enable_thinking": False}  # GLM and Muse: answer straight away


def _json_from(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S)
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("NVIDIA model returned no JSON", "bad_output")
    return json.loads(content[start:end + 1])


class Nvidia:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))  # a racer: fast or not at all
        self.health: dict | None = None
        self._fails: dict[str, int] = {}
        self._rest_until: dict[str, float] = {}

    @staticmethod
    def _keys(model: str | None = None) -> list[str]:
        """The keys to try for `model`: its own first, then the shared ones (no duplicates)."""
        names = ([MODEL_KEYS[model]] if model in MODEL_KEYS else []) + SHARED_KEYS
        if model is None:
            names = KEYS
        keys: list[str] = []
        for k in (get_secret(n) for n in names):
            if k and k not in keys:
                keys.append(k)
        return keys

    def models(self) -> list[str]:
        """The models that have a key and aren't resting. PLAG races all of them: with its full prompt each one swung
        between 1.5 s and 11 s (2026-09-24), so the first to answer is usually much faster than any single one."""
        now = time.time()
        return [m for m in MODELS if now >= self._rest_until.get(m, 0) and self._keys(m)]

    def ready(self) -> bool:
        return bool(self.models())

    def _failed(self, model: str, why: str, t0: float) -> None:
        self.health = {"ok": False, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time(), "error": why, "model": model}
        self._fails[model] = self._fails.get(model, 0) + 1
        if self._fails[model] >= 2:
            self._rest_until[model], self._fails[model] = time.time() + 300, 0

    async def turn(self, *, system: str, history: list[tuple[str, str]], text: str, schema: dict | None = None,
                   max_tokens: int = 700, model: str | None = None, timeout: float | None = None,
                   think: bool = False) -> tuple[str, dict, int]:
        """One JSON answer with the keys of `schema` (PLAG's command shape by default), from `model` or the first
        one that isn't resting. `timeout` is for long answers (writing); racers keep the short default.
        `think`: let the model reason before it answers (GLM and Muse think first, gpt-oss reasons more): slower,
        better for plans, reports and hard questions."""
        model = model or next(iter(self.models()), None)
        keys = self._keys(model) if model else []
        if not keys or not model:
            raise ProviderError("NVIDIA models are resting or no key is saved", "unavailable")
        wanted = ", ".join(((schema or {}).get("properties") or {}).keys()) or _COMMAND_KEYS
        messages = [{"role": "system", "content": f"{system}\n\nOutput ONLY one JSON object with the keys {wanted}. No markdown."}]
        messages += [{"role": "assistant" if role == "model" else "user", "content": t} for role, t in history]
        messages.append({"role": "user", "content": text})
        body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens, "stream": False}
        if model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "medium" if think else "low"  # "low" keeps its reasoning to a sentence or two
        elif model in MODEL_KEYS:
            body["chat_template_kwargs"] = {"thinking": True, "enable_thinking": True} if think else _NO_THINKING
            if not think:
                body["reasoning_effort"] = "low"
        if think:
            body["max_tokens"] = max(max_tokens, 4000)  # the reasoning uses tokens before the answer starts
            timeout = timeout or 60.0
        t0 = time.perf_counter()
        r = None
        for key in keys:
            try:
                r = await self._http.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body,
                                          timeout=httpx.Timeout(timeout, connect=5.0) if timeout else httpx.USE_CLIENT_DEFAULT)
            except httpx.TimeoutException as e:
                self._failed(model, "timeout", t0)
                raise ProviderError(f"{model} timed out", "timeout") from e
            except httpx.TransportError as e:
                self._failed(model, "offline", t0)
                raise ProviderError("NVIDIA unreachable", "offline") from e
            if r.status_code not in (401, 403, 429):
                break  # anything else isn't about the key: another key won't help
        if r is None or r.status_code != 200:
            code = r.status_code if r is not None else "no_key"
            self._failed(model, str(code), t0)
            raise ProviderError(f"{model} HTTP {code}", code)
        try:
            obj = _json_from(r.json()["choices"][0]["message"].get("content") or "")
        except (KeyError, IndexError, ValueError) as e:
            self._failed(model, "bad_output", t0)
            raise ProviderError(f"{model} answer unreadable", "bad_output") from e
        ms = int((time.perf_counter() - t0) * 1000)
        self.health = {"ok": True, "ms": ms, "at": time.time(), "error": None, "model": model}
        self._fails[model] = 0
        return model, obj, ms


nvidia = Nvidia()
