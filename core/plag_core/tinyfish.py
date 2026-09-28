"""TinyFish: web search, page reading and a web agent that works on real websites.

Three of TinyFish's APIs (docs.tinyfish.ai, checked 2026-09-28), all with the header X-API-Key:
- Search  GET  https://api.search.tinyfish.ai?query=...   -> {results: [{position, site_name, title, snippet, url}]}  free
- Fetch   POST https://api.fetch.tinyfish.ai {urls: [...]} -> {results: [{url, title, text}], errors}            free
- Agent   POST https://agent.tinyfish.ai/v1/automation/run-sse {url, goal} -> an SSE stream: STARTED, PROGRESS
  {purpose}, COMPLETE {status, result, error}. Runs in TinyFish's own cloud browser (never your accounts) and is
  metered from your TinyFish wallet, so PLAG only uses it when you ask for something done on a website.

PLAG tells the agent to only read and report: no buying, paying, signing in, submitting forms or messaging.
Everything it brings back is data: the AI is told never to follow instructions inside it.

Key: Windows Credential Manager, PLAG / tinyfish_api_key (agent.tinyfish.ai/api-keys).
"""

import asyncio
import json
import time

import httpx

from .secrets import get_secret

SEARCH = "https://api.search.tinyfish.ai"
FETCH = "https://api.fetch.tinyfish.ai"
AGENT = "https://agent.tinyfish.ai/v1/automation/run-sse"
KEY = "tinyfish_api_key"
READ_ONLY = (" Only read and report what you find. Do not buy, pay, book, sign in, create accounts, submit forms, post, "
             "or send messages. If the page needs any of that, stop and say so. Return the answer as JSON.")


class TinyFishError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class TinyFish:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))
        self._rest_until = 0.0
        self.health: dict | None = None
        self.last_error: str | None = None

    @staticmethod
    def key() -> str | None:
        return get_secret(KEY)

    def ready(self) -> bool:
        return bool(self.key()) and time.time() >= self._rest_until

    def _failed(self, code: str) -> None:
        self.last_error = code
        if code in ("401", "403"):
            self._rest_until = time.time() + 900  # a refused key: don't keep knocking
        self.health = {"ok": False, "error": code, "at": time.time()}

    async def search(self, query: str, limit: int = 6, news: bool = False, location: str = "IN") -> list[dict]:
        """[{title, url, site, snippet, date}]; empty when there's no key or it failed (callers carry on without it)."""
        key = self.key()
        if not key or not self.ready() or not query.strip():
            return []
        params = {"query": query[:500], "location": location, "language": "en"}
        if news:
            params["domain_type"] = "news"
        t0 = time.perf_counter()
        try:
            r = await self._http.get(SEARCH, params=params, headers={"X-API-Key": key})
        except httpx.HTTPError:
            self._failed("offline")
            return []
        if r.status_code != 200:
            self._failed(str(r.status_code))
            return []
        out = []
        for it in (r.json().get("results") or [])[:limit]:
            url = str(it.get("url") or "")
            if not url.startswith(("https://", "http://")):
                continue
            out.append({"title": str(it.get("title") or "")[:200], "url": url,
                        "site": str(it.get("site_name") or url.split("/")[2]).removeprefix("www.")[:60],
                        "snippet": " ".join(str(it.get("snippet") or "").split())[:400],
                        "date": str(it.get("date") or "")[:40]})
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time(), "what": "search"}
        self.last_error = None
        return out

    async def fetch(self, urls: list[str], chars: int = 2500, timeout_s: float = 12.0) -> dict[str, str]:
        """{url: page text as Markdown} for the pages that could be read in time."""
        key = self.key()
        urls = [u for u in urls if u.startswith(("https://", "http://"))][:5]
        if not key or not self.ready() or not urls:
            return {}
        try:
            r = await self._http.post(FETCH, headers={"X-API-Key": key}, timeout=httpx.Timeout(timeout_s + 3, connect=5.0),
                                      json={"urls": urls, "format": "markdown", "per_url_timeout_ms": int(timeout_s * 1000)})
        except httpx.HTTPError:
            return {}
        if r.status_code != 200:
            self._failed(str(r.status_code))
            return {}
        return {str(it.get("url")): " ".join(str(it.get("text") or "").split())[:chars]
                for it in (r.json().get("results") or []) if it.get("text")}

    async def research(self, query: str, limit: int = 4) -> list[dict]:
        """Search, then read the top pages: [{title, url, site, content}] (content = page text, or the snippet)."""
        hits = await self.search(query, limit)
        if not hits:
            return []
        try:
            pages = await asyncio.wait_for(self.fetch([h["url"] for h in hits[:2]], chars=900, timeout_s=6.0), 8)
        except TimeoutError:
            pages = {}
        return [{**h, "content": pages.get(h["url"]) or h["snippet"]} for h in hits]

    async def run(self, url: str, goal: str, progress=None, timeout_s: float = 150.0) -> dict:
        """The web agent does `goal` on `url` and reports back: {result, status, steps, ms, run_id}.
        `progress(text)` hears what it's doing ("Clicking the search box"). Raises TinyFishError."""
        key = self.key()
        if not key:
            raise TinyFishError("TinyFish isn't set up: save your key as PLAG / tinyfish_api_key.", "no_key")
        if not url.startswith("https://"):
            raise TinyFishError("The web agent needs a website address that starts with https://.", "bad_url")
        t0 = time.perf_counter()
        body = {"url": url, "goal": goal.strip()[:1500] + READ_ONLY, "agent_config": {"max_duration_seconds": int(timeout_s)}}
        steps, run_id, done = 0, "", None
        try:
            async with self._http.stream("POST", AGENT, headers={"X-API-Key": key}, json=body,
                                         timeout=httpx.Timeout(timeout_s + 30, connect=8.0)) as r:
                if r.status_code != 200:
                    self._failed(str(r.status_code))
                    msg = {401: "TinyFish didn't accept the key.", 403: "TinyFish refused that run.",
                           429: "TinyFish is busy (too many runs at once). Try again in a minute."}.get(
                        r.status_code, f"TinyFish returned an error ({r.status_code}).")
                    raise TinyFishError(msg, str(r.status_code))
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    kind = ev.get("type")
                    run_id = ev.get("run_id") or run_id
                    if kind == "PROGRESS":
                        steps += 1
                        if progress and ev.get("purpose"):
                            progress(str(ev["purpose"])[:120])
                    elif kind == "COMPLETE":
                        done = ev
                        break
        except httpx.TimeoutException as e:
            raise TinyFishError("The web agent took too long, so PLAG stopped waiting.", "timeout") from e
        except httpx.HTTPError as e:
            self._failed("offline")
            raise TinyFishError("TinyFish can't be reached right now.", "offline") from e
        ms = int((time.perf_counter() - t0) * 1000)
        if done is None:
            raise TinyFishError("The web agent stopped without an answer.", "no_result")
        if done.get("status") != "COMPLETED":
            err = done.get("error") or {}
            raise TinyFishError(str(err.get("message") or err.get("help_message") or "The web agent couldn't finish.")[:200],
                                str(err.get("code") or "failed"))
        result = done.get("result")
        text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        # "COMPLETED" doesn't always mean it worked (TinyFish's own docs): look for the tell-tale words
        if any(w in (text or "").lower() for w in ("captcha", "access denied", "blocked", "please sign in", "log in to continue")):
            raise TinyFishError("The website blocked the web agent (a captcha or a sign-in wall).", "blocked")
        self.health = {"ok": True, "ms": ms, "at": time.time(), "what": "agent"}
        return {"result": result, "text": (text or "")[:3000], "steps": steps, "ms": ms, "run_id": run_id}

    async def check_key(self, key: str) -> None:
        """One free search to prove a key works (raises TinyFishError otherwise)."""
        try:
            r = await self._http.get(SEARCH, params={"query": "PLAG assistant", "location": "IN"}, headers={"X-API-Key": key.strip()})
        except httpx.HTTPError as e:
            raise TinyFishError("TinyFish can't be reached right now. Check the internet and try again.", "offline") from e
        if r.status_code in (401, 403):
            raise TinyFishError("TinyFish didn't accept that key. Copy it again from agent.tinyfish.ai/api-keys.", "bad_key")
        if r.status_code != 200:
            raise TinyFishError(f"TinyFish answered with an error ({r.status_code}). Try again in a minute.", str(r.status_code))
        self._rest_until, self.last_error = 0.0, None


tinyfish = TinyFish()
