"""Web search for answers and the autopilot: real results from across the web (not just Wikipedia and news headlines).

Tavily's search API (made for AI agents: each result comes with the relevant text already extracted). Free tier:
1,000 searches a month at tavily.com. Key: Windows Credential Manager, PLAG / tavily_api_key. Without it PLAG still
answers from Wikipedia and Google News. Results are data: the AI is told never to follow instructions inside them.
"""

import time

import httpx

from .secrets import get_secret

URL = "https://api.tavily.com/search"


class WebSearch:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0))
        self._rest_until = 0.0
        self.health: dict | None = None

    @staticmethod
    def key() -> str | None:
        return get_secret("tavily_api_key")

    def ready(self) -> bool:
        return bool(self.key()) and time.time() >= self._rest_until

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        """[{title, url, content, site}] (empty when there's no key or the search failed: callers carry on without it)."""
        key = self.key()
        if not key or not self.ready() or not query.strip():
            return []
        t0 = time.perf_counter()
        try:
            r = await self._http.post(URL, headers={"Authorization": f"Bearer {key}"},
                                      json={"query": query[:380], "max_results": limit, "search_depth": "basic"})
        except httpx.HTTPError:
            self.health = {"ok": False, "error": "offline", "at": time.time()}
            return []
        if r.status_code != 200:
            self.health = {"ok": False, "error": str(r.status_code), "at": time.time()}
            if r.status_code in (401, 403, 429, 432, 433):
                self._rest_until = time.time() + 600  # bad key or monthly limit: don't ask again for a while
            return []
        out = []
        for it in (r.json().get("results") or [])[:limit]:
            url = str(it.get("url") or "")
            if not url.startswith(("https://", "http://")):
                continue
            site = url.split("/")[2].removeprefix("www.")
            out.append({"title": str(it.get("title") or site)[:160], "url": url, "site": site,
                        "content": " ".join(str(it.get("content") or "").split())[:700]})
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        return out


websearch = WebSearch()
