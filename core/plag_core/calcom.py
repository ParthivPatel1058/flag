"""Cal.com: your meetings, free slots, booking links, and booking / cancelling / rescheduling by voice.

Cal.com API v2 (cal.com/docs/api-reference/v2, checked 2026-09-28): https://api.cal.com/v2, header
Authorization: Bearer <cal_live_… key>, and a cal-api-version header per endpoint (a wrong or missing one silently
falls back to an older version of that endpoint):
  GET  /me                                  your profile (username, time zone)
  GET  /event-types?username=               your event types                 cal-api-version 2024-06-14
  GET  /slots?eventTypeId=&start=&end=       free slots                      cal-api-version 2024-09-04
  GET  /bookings?status=upcoming            your bookings                    cal-api-version 2026-05-01
  POST /bookings                            book a slot                      cal-api-version 2024-08-13
  POST /bookings/{uid}/cancel               cancel                           cal-api-version 2026-02-25
  POST /bookings/{uid}/reschedule           move it                          cal-api-version 2026-02-25
Booking, cancelling and rescheduling email the other person, so PLAG asks you first (tools.py: level L2, approval).

Key: Windows Credential Manager, PLAG / calcom_api_key (Cal.com → Settings → Security → API keys).
"""

import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from .secrets import get_secret

API = "https://api.cal.com/v2"
KEY = "calcom_api_key"
V_EVENT_TYPES, V_SLOTS, V_BOOKINGS_LIST, V_BOOK, V_CHANGE = "2024-06-14", "2024-09-04", "2026-05-01", "2024-08-13", "2026-02-25"


class CalError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def to_utc(local_iso: str) -> str:
    """ "2026-09-30T17:00:00" (laptop's local time) -> "2026-09-30T11:30:00Z"."""
    t = datetime.fromisoformat(local_iso.strip().replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.astimezone()  # the laptop's own time zone
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def say_time(iso: str) -> str:
    """ "today 5:00 PM" / "tomorrow 11:30 AM" / "Fri 3 Oct 4:00 PM"."""
    t, today = local(iso), datetime.now().date()
    clock = t.strftime("%I:%M %p").lstrip("0")
    if t.date() == today:
        return f"today {clock}"
    if t.date() == today + timedelta(days=1):
        return f"tomorrow {clock}"
    return f"{t.strftime('%a %d %b')} {clock}"


class CalCom:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._me: tuple[dict, float] | None = None
        self._types: tuple[list[dict], float] | None = None
        self.health: dict | None = None
        self.last_error: str | None = None

    @staticmethod
    def key() -> str | None:
        return get_secret(KEY)

    def configured(self) -> bool:
        return bool(self.key())

    async def _call(self, method: str, path: str, version: str | None = None, params: dict | None = None,
                    json: dict | None = None, key: str | None = None) -> dict | list:
        key = key or self.key()
        if not key:
            raise CalError("Cal.com isn't connected: save your key as PLAG / calcom_api_key.", "no_key")
        headers = {"Authorization": f"Bearer {key}"}
        if version:
            headers["cal-api-version"] = version
        t0 = time.perf_counter()
        try:
            r = await self._http.request(method, f"{API}{path}", headers=headers, params=params, json=json)
        except httpx.HTTPError as e:
            self.last_error = "offline"
            raise CalError("Cal.com can't be reached right now.", "offline") from e
        data = r.json() if r.content and "json" in r.headers.get("content-type", "") else {}
        if r.status_code >= 400 or (isinstance(data, dict) and data.get("status") == "error"):
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            msg = str(err.get("message") or (data.get("message") if isinstance(data, dict) else "") or "")[:200]
            code = {401: "bad_key", 403: "forbidden", 404: "not_found", 429: "busy"}.get(r.status_code, str(r.status_code))
            self.last_error = code
            if code == "bad_key":
                raise CalError("Cal.com didn't accept the key. Make a new one in Cal.com → Settings → Security.", code)
            raise CalError(f"Cal.com said: {msg or 'error ' + str(r.status_code)}.", code)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        self.last_error = None
        return data.get("data", data) if isinstance(data, dict) else data

    # ---------------------------------------------------------------- reading

    async def me(self, fresh: bool = False) -> dict:
        if self._me and not fresh and time.time() - self._me[1] < 600:
            return self._me[0]
        d = await self._call("GET", "/me")
        me = {"username": d.get("username") or "", "email": d.get("email") or "", "name": d.get("name") or "",
              "time_zone": d.get("timeZone") or "Asia/Kolkata"}
        self._me = (me, time.time())
        return me

    async def event_types(self) -> list[dict]:
        """[{id, slug, title, length, hidden}] of your own event types."""
        if self._types and time.time() - self._types[1] < 600:
            return self._types[0]
        me = await self.me()
        data = await self._call("GET", "/event-types", V_EVENT_TYPES, params={"username": me["username"]} if me["username"] else None)
        items = data if isinstance(data, list) else (data.get("eventTypes") or []) if isinstance(data, dict) else []
        types = [{"id": t.get("id"), "slug": t.get("slug") or "", "title": t.get("title") or t.get("slug") or "Meeting",
                  "length": t.get("lengthInMinutes") or t.get("length") or 30, "hidden": bool(t.get("hidden"))}
                 for t in items if t.get("id")]
        self._types = (types, time.time())
        return types

    async def pick_type(self, hint: str = "") -> dict:
        """The event type that matches `hint` ("30 min", "intro call"), else your first visible one."""
        types = [t for t in await self.event_types() if not t["hidden"]] or await self.event_types()
        if not types:
            raise CalError("Your Cal.com account has no event types yet. Create one on cal.com first.", "no_event_types")
        h = (hint or "").casefold()
        if m := re.search(r"(\d{1,3})\s*(?:min|minute|mins|m\b)", h):
            for t in types:
                if t["length"] == int(m[1]):
                    return t
        for t in types:
            if h and (t["title"].casefold() in h or t["slug"].casefold() in h or h in t["title"].casefold()):
                return t
        return types[0]

    async def link(self, hint: str = "") -> str:
        me = await self.me()
        if not me["username"]:
            raise CalError("Your Cal.com profile has no username, so there's no public link.", "no_username")
        if hint:
            t = await self.pick_type(hint)
            return f"https://cal.com/{me['username']}/{t['slug']}"
        return f"https://cal.com/{me['username']}"

    async def slots(self, day: str = "today", hint: str = "", days: int = 1) -> tuple[dict, list[str]]:
        """Free start times: (event type, ["2026-09-30T11:30:00Z", ...]) for `day` (today|tomorrow|YYYY-MM-DD)."""
        t = await self.pick_type(hint)
        start = datetime.now().date() + timedelta(days=1 if day == "tomorrow" else 0)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
            start = datetime.fromisoformat(day).date()
        end = start + timedelta(days=max(1, days) - 1)
        me = await self.me()
        data = await self._call("GET", "/slots", V_SLOTS, params={
            "eventTypeId": t["id"], "start": start.isoformat(), "end": end.isoformat(), "timeZone": me["time_zone"]})
        out: list[str] = []
        for _date, items in (data.items() if isinstance(data, dict) else []):
            for s in items or []:
                iso = s.get("start") if isinstance(s, dict) else s
                if iso and local(iso) > datetime.now() + timedelta(minutes=10):
                    out.append(iso)
        return t, sorted(out, key=local)

    async def bookings(self, status: str = "upcoming", day: str = "", limit: int = 20) -> list[dict]:
        """Your bookings: [{uid, title, start, end, who, emails, url, status}] (day = today|tomorrow narrows it)."""
        params: dict = {"status": status, "limit": limit, "sortStart": "asc" if status != "past" else "desc"}
        if day in ("today", "tomorrow"):
            d0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1 if day == "tomorrow" else 0)
            params["afterStart"] = to_utc(d0.isoformat())
            params["beforeEnd"] = to_utc((d0 + timedelta(days=1)).isoformat())
        data = await self._call("GET", "/bookings", V_BOOKINGS_LIST, params=params)
        out = []
        for b in data if isinstance(data, list) else []:
            people = [a for a in (b.get("attendees") or []) if isinstance(a, dict)]
            loc = b.get("meetingUrl") or b.get("location") or ""
            out.append({"uid": b.get("uid") or "", "title": b.get("title") or "Meeting", "start": b.get("start") or "",
                        "end": b.get("end") or "", "who": ", ".join(a.get("name") or a.get("email") or "" for a in people)[:120],
                        "emails": [a.get("email") for a in people if a.get("email")],
                        "url": loc if isinstance(loc, str) and loc.startswith("https://") else "", "status": b.get("status") or status})
        return [b for b in out if b["uid"] and b["start"]]

    async def find(self, query: str) -> dict | None:
        """The upcoming booking whose attendee or title matches `query` ("Rahul", "the demo")."""
        q = (query or "").casefold().strip()
        for b in await self.bookings("upcoming", limit=50):
            hay = f"{b['who']} {b['title']} {' '.join(b['emails'])}".casefold()
            if q and (q in hay or any(w in hay for w in q.split() if len(w) > 2)):
                return b
        return None

    # ---------------------------------------------------------------- changing (tools.py asks you first)

    async def book(self, event_type_id: int, start_utc: str, name: str, email: str, notes: str = "") -> dict:
        me = await self.me()
        body: dict = {"start": start_utc, "eventTypeId": int(event_type_id),
                      "attendee": {"name": name[:80], "email": email, "timeZone": me["time_zone"], "language": "en"}}
        if notes:
            body["bookingFieldsResponses"] = {"notes": notes[:500]}
        b = await self._call("POST", "/bookings", V_BOOK, json=body)
        b = b if isinstance(b, dict) else {}
        return {"uid": b.get("uid") or "", "title": b.get("title") or "Meeting", "start": b.get("start") or start_utc,
                "url": b.get("meetingUrl") or ""}

    async def cancel(self, uid: str, reason: str = "") -> None:
        await self._call("POST", f"/bookings/{uid}/cancel", V_CHANGE, json={"cancellationReason": reason[:200] or "Cancelled by PLAG for the host"})

    async def reschedule(self, uid: str, start_utc: str, reason: str = "") -> dict:
        b = await self._call("POST", f"/bookings/{uid}/reschedule", V_CHANGE,
                             json={"start": start_utc, "reschedulingReason": reason[:200] or "Rescheduled by the host"})
        b = b if isinstance(b, dict) else {}
        return {"uid": b.get("uid") or uid, "start": b.get("start") or start_utc}

    async def check_key(self, key: str) -> dict:
        d = await self._call("GET", "/me", key=key.strip())
        self._me, self._types = None, None
        return {"username": d.get("username") or "", "email": d.get("email") or ""}


calcom = CalCom()
