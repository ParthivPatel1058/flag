"""Tool registry. Every call goes: policy gate -> execute with timeout -> verify -> audit.

Every result says what state it reached (`state`), not just "done": opening a page isn't the page being ready, so
browser and app steps watch the real window titles until the expected page or app shows up ("page_ready",
"app_ready"), and say "opened_unverified" when it didn't within the time limit.
"""

import asyncio
import ctypes
import os
import re
import subprocess
import time
import urllib.parse
from ctypes import wintypes

import httpx
import psutil
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import whatsapp
from .audit import audit
from .catalog import APPS
from .policy import Level, policy
from .system import sampler

_PRIVATE_ARGS = {"message"}  # never written to the audit log verbatim

# PLAG_DRY_RUN=1: the whole pipeline runs (understand, policy, audit, reply) but nothing opens or gets sent.
# Used by the test suite.
DRY_RUN = os.environ.get("PLAG_DRY_RUN") == "1"
_SIDE_EFFECTS = {"open_url", "open_app", "web_search", "play_youtube", "whatsapp_send", "whatsapp_call", "whatsapp_open"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    level: Level
    summary: str
    timeout_s: float = 10.0


@dataclass
class ToolResult:
    ok: bool
    detail: str = ""
    data: dict = field(default_factory=dict)
    state: str = ""  # machine-readable: page_ready, opened_unverified, app_ready, chat_open, sent, timeout, failed…


_REGISTRY: dict[str, tuple[ToolSpec, Callable[..., Awaitable[ToolResult]]]] = {}


def tool(spec: ToolSpec):
    def register(fn):
        _REGISTRY[spec.name] = (spec, fn)
        return fn
    return register


def spec_of(name: str) -> ToolSpec:
    return _REGISTRY[name][0]


async def run_tool(name: str, args: dict, *, task_id: str, origin: str, approved: bool = False) -> ToolResult:
    spec, fn = _REGISTRY[name]
    policy.check(spec.level, approved=approved)  # raises Halted / NeedsApproval / Forbidden before anything runs
    t0 = time.perf_counter()
    try:
        if DRY_RUN and name in _SIDE_EFFECTS:
            result = ToolResult(True, f"dry run: {name}", {"mode": "desktop"}, state="dry_run")
        else:
            result = await asyncio.wait_for(fn(**args), spec.timeout_s)
    except asyncio.TimeoutError:
        result = ToolResult(False, "took too long, so I stopped it", state="timeout")
    except Exception as e:  # a failing tool reports; it never crashes the turn
        result = ToolResult(False, str(e)[:160] or type(e).__name__, state="failed")
    if not result.state:
        result.state = "done" if result.ok else "failed"
    ms = int((time.perf_counter() - t0) * 1000)
    logged = {k: (f"<{len(str(v))} chars>" if k in _PRIVATE_ARGS else v) for k, v in args.items()}
    audit("tool.call", task=task_id, tool=name, level=int(spec.level), args=logged, origin=origin,
          approved=approved, ok=result.ok, state=result.state, detail=result.detail, ms=ms)
    return result


# ---------------------------------------------------------------- verification: what's actually on screen

BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe")
_user32 = ctypes.WinDLL("user32")
VERIFY_S = 8.0  # a cold browser start takes 2-4 s here; a warm tab 0.5-1.5 s


def window_titles(processes: tuple[str, ...] | None = None) -> list[str]:
    """Titles of the visible top-level windows, optionally only those of the given programs."""
    titles: list[str] = []
    names: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        if processes:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in names:
                try:
                    names[pid.value] = psutil.Process(pid.value).name().lower()
                except psutil.Error:
                    names[pid.value] = ""
            if names[pid.value] not in processes:
                return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        titles.append(buf.value)
        return True

    _user32.EnumWindows(each, 0)
    return titles


def _still_loading(title: str) -> bool:
    """While a tab loads, browsers show its address as the title ("youtube.com/results?search_query=…")."""
    head = title.split(" - ")[0]
    return "://" in head or "?" in head or bool(re.search(r"\w\.\w{2,}/", head))


async def wait_for_title(matches: Callable[[str], bool], timeout: float = VERIFY_S,
                         processes: tuple[str, ...] | None = BROWSERS, ignore: set[str] | None = None) -> str | None:
    """Poll window titles until one matches (lower-cased) and the page has its own title, not its address.
    Titles in `ignore` (what was on screen before the step) don't count: an old tab can't pass for the new one."""
    end = time.monotonic() + timeout
    while True:
        for title in await asyncio.to_thread(window_titles, processes):
            t = title.casefold()
            if matches(t) and not _still_loading(t) and not (ignore and title in ignore):
                return title
        if time.monotonic() >= end:
            return None
        await asyncio.sleep(0.25)


def _site_word(url: str) -> str:
    """The word a page's title will show: google.com -> "google", mail.google.com -> "gmail"."""
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    if host.startswith("mail.google."):
        return "gmail"
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host


async def _launch(url: str, browser: str) -> None:
    chrome = _chrome_path() if browser == "chrome" else None
    if chrome:
        await asyncio.to_thread(subprocess.Popen, [chrome, url], creationflags=subprocess.DETACHED_PROCESS)
    else:
        await asyncio.to_thread(os.startfile, url)


async def _open_verified(url: str, matches: Callable[[str], bool], browser: str = "default") -> ToolResult:
    """Open a page and wait until the browser actually shows it. Retries once only if no browser window appeared
    at all (the launch didn't take); a browser showing something else is reported, not opened twice."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return ToolResult(False, "blocked: only http and https links can be opened", state="blocked")
    before = set(await asyncio.to_thread(window_titles, BROWSERS))
    for attempt in (1, 2):
        await _launch(url, browser)
        if title := await wait_for_title(matches, ignore=before):
            return ToolResult(True, f"page ready: {title[:90]}", {"url": url, "title": title}, state="page_ready")
        if attempt == 2 or await asyncio.to_thread(window_titles, BROWSERS):
            break
    return ToolResult(True, "opened, but I couldn't confirm the page loaded", {"url": url}, state="opened_unverified")


# ---------------------------------------------------------------- tools

@tool(ToolSpec("open_url", Level.LOW, "Open a web page in the default browser", timeout_s=25))
async def open_url(url: str) -> ToolResult:
    word = _site_word(url)
    return await _open_verified(url, lambda t: word in t or (word == "gmail" and "inbox" in t))


def _chrome_path() -> str | None:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
        except OSError:
            pass
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("LOCALAPPDATA", "")):
        path = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if base and os.path.exists(path):
            return path
    return None


def _query_words(query: str) -> list[str]:
    """The query's key words (up to 3), all of which a results page's title must show."""
    words = [w for w in re.findall(r"\w+", query.casefold()) if len(w) >= 3] or re.findall(r"\w+", query.casefold())
    return words[:3]


def _query_word(query: str) -> str:
    words = _query_words(query)
    return words[0] if words else ""


@tool(ToolSpec("web_search", Level.LOW, "Search Google or YouTube in the browser", timeout_s=25))
async def web_search(site: str, query: str, browser: str = "default") -> ToolResult:
    base = {"google": "https://www.google.com/search?q=",
            "youtube": "https://www.youtube.com/results?search_query="}.get(site)
    if not base or not query.strip():
        return ToolResult(False, "unsupported search", state="failed")
    words = _query_words(query)
    # results are showing when the tab title is "<query> - Google Search" / "<query> - YouTube"
    marker = "google search" if site == "google" else "youtube"
    result = await _open_verified(base + urllib.parse.quote_plus(query.strip()),
                                  lambda t: marker in t and all(w in t for w in words), browser)
    if result.state == "page_ready":
        result.state, result.detail = "results_ready", f"results showing: {result.data['title'][:90]}"
    return result


async def _open_in(url: str, browser: str) -> ToolResult:
    return await _open_verified(url, lambda t: "youtube" in t, browser)


_VIDEO_ID = re.compile(r'"videoId":"([\w-]{11})"')


@tool(ToolSpec("play_youtube", Level.LOW, "Find a video on YouTube and start playing it", timeout_s=30))
async def play_youtube(query: str, browser: str = "default") -> ToolResult:
    """Opens the top result on autoplay; falls back to the results page if YouTube can't be read."""
    q = urllib.parse.quote_plus(query.strip())
    m = None
    try:
        # The results page is ~1 MB; on a busy laptop downloading all of it took over 6 s. Read it as it arrives and
        # stop at the first video (usually within the first few hundred KB).
        async with httpx.AsyncClient(timeout=10.0, headers={"Accept-Language": "en-US,en;q=0.9"}) as http:
            async with http.stream("GET", f"https://www.youtube.com/results?search_query={q}") as r:
                page = ""
                async for chunk in r.aiter_text():
                    page += chunk
                    if m := _VIDEO_ID.search(page[-len(chunk) - 40:]):
                        break
    except httpx.HTTPError:
        m = None
    if m:
        result = await _open_in(f"https://www.youtube.com/watch?v={m.group(1)}&autoplay=1", browser)
        if result.state == "page_ready":
            result.state, result.detail = "playing", f"playing: {result.data['title'][:90]}"
        return result
    result = await _open_in(f"https://www.youtube.com/results?search_query={q}", browser)
    result.detail = "opened the results; couldn't pick a video automatically"
    return result


@tool(ToolSpec("open_app", Level.LOW, "Launch a known desktop app", timeout_s=20))
async def open_app(app: str) -> ToolResult:
    entry = APPS.get(app)
    if entry is None:
        return ToolResult(False, "unknown app", state="failed")
    try:
        await asyncio.to_thread(os.startfile, entry.target)
    except OSError:
        return ToolResult(False, f"{entry.name} isn't installed or Windows couldn't start it", state="failed")
    # the app is ready when a window with its name is showing ("Untitled - Notepad", "WhatsApp", "Calculator")
    name = entry.name.casefold()
    if title := await wait_for_title(lambda t: name in t, processes=None):  # an already-open app window counts: it's open
        return ToolResult(True, f"{entry.name} is open", {"title": title}, state="app_ready")
    return ToolResult(True, f"started {entry.name}, but its window didn't show up yet", state="opened_unverified")


# L1 by the user's choice: PLAG sends without asking. whatsapp.py checks each step so it only types into the chat
# whose name matches, and stops if you switch windows. Halt still stops it.
@tool(ToolSpec("whatsapp_send", Level.LOW, "Send a WhatsApp message to anyone in your WhatsApp, by name or number",
               timeout_s=45))
async def whatsapp_send(to: str, message: str) -> ToolResult:
    r = await whatsapp.send(to, message)
    return ToolResult(r["ok"], r["detail"], {"name": r["name"], "options": r["options"]},
                      state="sent" if r["ok"] else "user_required" if r["options"] else "failed")


@tool(ToolSpec("whatsapp_call", Level.LOW, "Start a WhatsApp voice or video call with anyone in your WhatsApp",
               timeout_s=45))
async def whatsapp_call(to: str, video: bool = False) -> ToolResult:
    r = await whatsapp.call(to, video=video)
    return ToolResult(r["ok"], r["detail"], {"name": r["name"], "options": r["options"]},
                      state="calling" if r["ok"] else "user_required" if r["options"] else "failed")


@tool(ToolSpec("whatsapp_open", Level.LOW, "Open the WhatsApp chat of anyone in your WhatsApp", timeout_s=40))
async def whatsapp_open(to: str) -> ToolResult:
    r = await whatsapp.open_chat(to)
    return ToolResult(r["ok"], r["detail"], {"name": r["name"], "options": r["options"]},
                      state="chat_open" if r["ok"] else "user_required" if r["options"] else "failed")


@tool(ToolSpec("system_status", Level.READ, "Read CPU, memory, disk, battery and the heaviest apps"))
async def system_status() -> ToolResult:
    if time.monotonic() - sampler.top_at > 4:
        # CPU per app needs two readings apart in time; the dashboard's live panel normally keeps these fresh
        await asyncio.to_thread(sampler.processes)
        await asyncio.sleep(0.5)
        top = await asyncio.to_thread(sampler.processes)
    else:
        top = sampler.top
    sample = await asyncio.to_thread(sampler.sample)
    return ToolResult(True, f"CPU {sample['cpu']:.0f}% · memory {sample['mem']['pct']:.0f}%", {"sample": sample, "top": top})


# ---------------------------------------------------------------- Cal.com: these email the other person, so L2 (asks you)

from .calcom import CalError, calcom, say_time  # noqa: E402  (after the registry it registers into)


@tool(ToolSpec("cal_book", Level.EXTERNAL, "Book a Cal.com meeting (Cal.com emails the invite)", timeout_s=30))
async def cal_book(event_type_id: int, start: str, name: str, email: str, notes: str = "") -> ToolResult:
    try:
        b = await calcom.book(event_type_id, start, name, email, notes)
    except CalError as e:
        return ToolResult(False, str(e), state="failed")
    link = f" Meeting link: {b['url']}." if b["url"] else ""
    return ToolResult(True, f"Booked {b['title']} with {name}, {say_time(b['start'])}. Cal.com has emailed the invite.{link}",
                      {"uid": b["uid"]}, state="booked")


@tool(ToolSpec("cal_cancel", Level.EXTERNAL, "Cancel a Cal.com booking (Cal.com tells the attendee)", timeout_s=30))
async def cal_cancel(uid: str, label: str, reason: str = "") -> ToolResult:
    try:
        await calcom.cancel(uid, reason)
    except CalError as e:
        return ToolResult(False, str(e), state="failed")
    return ToolResult(True, f"Cancelled {label}. Cal.com has let them know.", {"uid": uid}, state="cancelled")


@tool(ToolSpec("cal_reschedule", Level.EXTERNAL, "Move a Cal.com booking to a new time (Cal.com tells the attendee)", timeout_s=30))
async def cal_reschedule(uid: str, start: str, label: str, reason: str = "") -> ToolResult:
    try:
        b = await calcom.reschedule(uid, start, reason)
    except CalError as e:
        return ToolResult(False, str(e), state="failed")
    return ToolResult(True, f"Moved {label} to {say_time(b['start'])}. Cal.com has let them know.", {"uid": b["uid"]},
                      state="rescheduled")


# ---------------------------------------------------------------- computer use: the one risky step you approved

@tool(ToolSpec("computer_continue", Level.EXTERNAL, "Press the button you approved, and carry on with the job",
               timeout_s=180))
async def computer_continue(session: str, label: str) -> ToolResult:
    from . import deskagent

    out = await deskagent.resume(session, True)
    if out.get("error"):
        return ToolResult(False, out["error"], state="failed")
    if out.get("needs_yes"):  # another risky button further along
        y = out["needs_yes"]
        return ToolResult(True, f"{y['summary']} (say “yes” again to go ahead)", {"needs_yes": y}, state="user_required")
    return ToolResult(bool(out.get("ok", True)), out.get("reply", f"Pressed {label}."), state="done")
