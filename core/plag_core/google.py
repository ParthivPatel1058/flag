"""Gmail and Google Calendar, read-only, through Google's official APIs and your own OAuth client.

Setup (once, by you): in Google Cloud Console create an OAuth client of type "Desktop app", enable the Gmail API and
the Google Calendar API, add your address as a test user, download the client JSON and pick it in PLAG. PLAG then
signs in through your browser. Scopes are read-only: PLAG can read email headers and calendar events, nothing else.

The client and the refresh token live in Windows Credential Manager (PLAG / google_client, google_refresh_token).
Email content is never handed to the AI: PLAG reads senders and subjects out itself, so an email can't instruct it.
Google expires sign-ins after 7 days while your OAuth app is in "Testing"; PLAG then asks you to connect again.
"""

import base64
import hashlib
import json
import os
import re
import secrets as pysecrets
import time
import urllib.parse
from datetime import datetime, timedelta

import httpx

from .secrets import delete_secret, get_secret, set_secret

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.readonly"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
CALLBACK_PATH = "/oauth/google"


class GoogleError(Exception):
    """Something the user can act on: not set up, not connected, needs signing in again."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class Google:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=6.0))
        self._access: tuple[str, float] | None = None  # (token, expires at)
        self._pending: dict[str, dict] = {}  # state -> {verifier, redirect, at}: sign-ins in progress
        self.email: str | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------ setup and sign-in

    @staticmethod
    def client() -> dict | None:
        raw = get_secret("google_client")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    @staticmethod
    def save_client(file_text: str) -> str:
        """Takes the JSON file Google Cloud Console downloads for a Desktop OAuth client. Returns the client id."""
        try:
            data = json.loads(file_text)
        except ValueError as e:
            raise GoogleError("That file isn't JSON. Pick the file Google Cloud Console downloaded.", "bad_client") from e
        inner = data.get("installed") or {}
        if not inner and data.get("web"):
            raise GoogleError("That's a Web client. Create an OAuth client of type Desktop app instead.", "bad_client")
        cid, secret = inner.get("client_id", ""), inner.get("client_secret", "")
        if not cid.endswith(".apps.googleusercontent.com") or not secret:
            raise GoogleError("That file doesn't contain a Desktop OAuth client ID and secret.", "bad_client")
        set_secret("google_client", json.dumps({"client_id": cid, "client_secret": secret}))
        return cid

    def connected(self) -> bool:
        return bool(get_secret("google_refresh_token"))

    def status(self) -> dict:
        return {"client": self.client() is not None, "connected": self.connected(), "email": self.email,
                "error": self.last_error}

    def begin(self, base_url: str) -> str:
        """Start signing in: returns the Google consent URL to open in the browser (PKCE, one-time state)."""
        client = self.client()
        if not client:
            raise GoogleError("Pick your Google OAuth client file first.", "no_client")
        now = time.time()
        self._pending = {s: p for s, p in self._pending.items() if now - p["at"] < 600}
        state = pysecrets.token_urlsafe(24)
        verifier = pysecrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect = base_url.rstrip("/") + CALLBACK_PATH
        self._pending[state] = {"verifier": verifier, "redirect": redirect, "at": now}
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent",
        })

    async def finish(self, state: str, code: str) -> str:
        """The browser came back from Google: swap the code for tokens. Returns the account's address."""
        pending = self._pending.pop(state or "", None)
        if not pending or time.time() - pending["at"] > 600:
            raise GoogleError("This sign-in link expired or was already used. Click Connect again.", "bad_state")
        client = self.client()
        if not client:
            raise GoogleError("The Google client is missing. Pick the client file again.", "no_client")
        r = await self._http.post(TOKEN_URL, data={
            "client_id": client["client_id"], "client_secret": client["client_secret"], "code": code,
            "code_verifier": pending["verifier"], "grant_type": "authorization_code", "redirect_uri": pending["redirect"],
        })
        data = r.json() if r.content else {}
        if r.status_code != 200 or "refresh_token" not in data:
            raise GoogleError(f"Google refused the sign-in ({data.get('error', r.status_code)}).", "token")
        set_secret("google_refresh_token", data["refresh_token"])
        self._access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60)
        self.last_error = None
        try:
            profile = await self._get(f"{GMAIL}/profile")
            self.email = profile.get("emailAddress")
        except GoogleError:
            self.email = None
        return self.email or "your account"

    async def disconnect(self) -> None:
        token = get_secret("google_refresh_token")
        if token:
            try:
                await self._http.post(REVOKE_URL, params={"token": token})
            except httpx.HTTPError:
                pass  # offline: forgetting it locally still disconnects PLAG
        delete_secret("google_refresh_token")
        self._access, self.email = None, None

    # ------------------------------------------------------------ calls

    async def _token(self) -> str:
        if self._access and time.time() < self._access[1]:
            return self._access[0]
        refresh, client = get_secret("google_refresh_token"), self.client()
        if not client:
            raise GoogleError("Google isn't set up yet. Open Connections and click Connect on Google.", "no_client")
        if not refresh:
            raise GoogleError("Google isn't connected yet. Open Connections and click Connect on Google.", "not_connected")
        try:
            r = await self._http.post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client["client_secret"],
                                                       "refresh_token": refresh, "grant_type": "refresh_token"})
        except httpx.HTTPError as e:
            raise GoogleError("Google can't be reached right now.", "offline") from e
        data = r.json() if r.content else {}
        if r.status_code != 200:
            if data.get("error") == "invalid_grant":  # revoked, or the 7-day limit of a Testing app
                delete_secret("google_refresh_token")
                self.last_error = "needs signing in again"
                raise GoogleError("Your Google connection needs signing in again. Click Connect on Google.", "expired")
            raise GoogleError(f"Google refused the request ({data.get('error', r.status_code)}).", "token")
        self._access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60)
        return self._access[0]

    async def _get(self, url: str, params: dict | list | None = None) -> dict:
        token = await self._token()
        try:
            r = await self._http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise GoogleError("Google can't be reached right now.", "offline") from e
        if r.status_code == 403:
            raise GoogleError("Google blocked that. Check the Gmail and Calendar APIs are enabled in your Cloud project.", "forbidden")
        if r.status_code != 200:
            raise GoogleError(f"Google returned an error ({r.status_code}).", "api")
        return r.json()

    async def emails(self, kind: str = "important", limit: int = 8, search: str = "") -> list[dict]:
        """Emails as {id, from, subject, snippet, date, unread}: important from the last day, unread in the inbox, or
        what a Gmail search finds (`kind="search"`, Gmail's own search syntax works, e.g. "from:rahul")."""
        query = search if kind == "search" else {"unread": "is:unread in:inbox newer_than:3d",
                                                 "today": "in:inbox newer_than:1d"}.get(kind, "is:important newer_than:1d")
        listing = await self._get(f"{GMAIL}/messages", {"q": query, "maxResults": limit})
        out = []
        for m in listing.get("messages", [])[:limit]:
            meta = await self._get(f"{GMAIL}/messages/{m['id']}",
                                   [("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "Subject")])
            headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
            sender = headers.get("from", "")
            name = re.sub(r'\s*<[^>]+>\s*$', "", sender).strip('" ') or sender
            out.append({"id": m["id"], "from": name[:60], "subject": (headers.get("subject") or "(no subject)")[:120],
                        "snippet": (meta.get("snippet") or "")[:200], "unread": "UNREAD" in meta.get("labelIds", []),
                        "date": datetime.fromtimestamp(int(meta.get("internalDate", "0")) / 1000).isoformat(timespec="minutes")})
        return out

    async def events(self, day: str = "today") -> list[dict]:
        """Calendar events for today or tomorrow as {summary, start, all_day, location}."""
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1 if day == "tomorrow" else 0)
        end = start + timedelta(days=1)
        tz = datetime.now().astimezone().tzinfo
        data = await self._get(CALENDAR, {
            "timeMin": start.replace(tzinfo=tz).isoformat(), "timeMax": end.replace(tzinfo=tz).isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 20,
        })
        out = []
        for e in data.get("items", []):
            s = e.get("start", {})
            out.append({"summary": (e.get("summary") or "(no title)")[:100], "all_day": "date" in s,
                        "start": s.get("dateTime") or s.get("date") or "", "location": (e.get("location") or "")[:80]})
        return out


google = Google()


def open_in_browser(url: str) -> None:
    os.startfile(url)
