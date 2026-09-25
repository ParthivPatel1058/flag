"""WhatsApp through the official desktop app: anyone in your WhatsApp, no contact list in PLAG, no confirmation.

WhatsApp has no API for personal accounts, so PLAG drives WhatsApp Desktop the way you would: bring it forward,
click the search box, type the name, open the best match, type the message, press Enter. Each step is checked
before the next key is pressed, so a message can only land in the chat it was meant for:
- WhatsApp must be the front window before every key press and click (switch away and PLAG stops),
- the search box must hold exactly the name PLAG typed,
- the chat that opens ("Type a message to <name>") must match the person you asked for,
- the message box must hold the message before Enter is pressed.
A phone number skips the search and uses WhatsApp's own click-to-chat link.

Measured on this laptop: walking WhatsApp's accessibility tree takes 10-20 s (the page appears in it several
times), so PLAG uses only two quick lookups: the first text box (the search box) and a hit-test on the spot where
the message box sits. WhatsApp also drops characters typed in one burst, so text goes in one character at a time.
"""

import asyncio
import ctypes
import difflib
import json
import os
import re
import threading
import time
import urllib.parse
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

import psutil

from .config import DATA_DIR
from .policy import policy

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

MATCH = 0.8  # how close a WhatsApp chat name must be to the name you said
# search results: where a row's chat name ends (its time or date, then the last message), and the section headers
_ROW_END = re.compile(r"\s+(?:\d{1,2}:\d{2}\s?(?:am|pm)?|\d{1,2}/\d{1,2}/\d{2,4}|yesterday|today"
                      r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b.*$", re.I)
_SECTIONS = {"chats", "contacts", "messages", "groups", "communities", "channels", "archived"}
COMPOSER = "Type a message to "
_HONORIFICS = {"ji", "bhai", "bhaiya", "sir", "madam", "mam", "didi", "bro", "my", "mera", "meri", "mere"}


class _Fail(Exception):
    """Stops a send with a reason the user hears. Raised before Enter, so nothing was sent."""


# ---------------------------------------------------------------- names and numbers

def normalize_phone(raw: str) -> str | None:
    """Digits in international form without '+'. Bare 10-digit numbers are treated as Indian mobiles."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        return "91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if 11 <= len(digits) <= 15:
        return digits
    return None


def _as_phone(to: str) -> str | None:
    return normalize_phone(to) if re.fullmatch(r"\+?[\d\s()-]{10,20}", (to or "").strip()) else None


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (text or "").casefold()).split()


def _short(word: str) -> str:
    """Long vowels written short, the way names are usually saved: "raahul" -> "rahul", "shotoo" -> "shotu"."""
    return word.replace("aa", "a").replace("ee", "i").replace("oo", "u")


def score(query: str, text: str) -> float:
    """How well the start of a WhatsApp chat name (or search result) matches the name you said, 0 to 1."""
    q = [w for w in _words(query) if w not in _HONORIFICS] or _words(query)
    t = _words(text)[: len(q) + 3]
    if not q or not t:
        return 0.0
    total = 0.0
    for w in q:
        best = 0.0
        for x in t:
            # whole words or a near spelling ("Rahool", "shotoo"); no prefixes, so "Priya" never picks "Priyanka"
            best = max(best, 1.0 if _short(x) == _short(w) else difflib.SequenceMatcher(None, _short(w), _short(x)).ratio())
        total += best
    return total / len(q)


def fits(query: str, title: str) -> bool:
    """The chat is the person you named: every word you said is in its name ("omi shotu" -> "Omi Bro Shotu
    Bangalore"), or every word of a longer name is in what you said ("omi bro shotu bangalore" -> "Omi Shotu").
    One word isn't enough the second way: "Omi" alone could be someone else."""
    if score(query, title) >= MATCH:
        return True
    return len(_name_words(title)) >= 2 and score(title, query) >= MATCH


# Names that worked before: "omi shotu" -> "Omi Bro Shotu Bangalore", so next time PLAG goes straight to the chat
ALIASES = DATA_DIR / "whatsapp_names.json"


def _name_key(name: str) -> str:
    return " ".join(_words(name))


def aliases() -> dict[str, str]:
    try:
        return json.loads(ALIASES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(said: str, title: str) -> None:
    """Remember that `said` means the chat `title` (only when they differ)."""
    if not said or not title or _name_key(said) == _name_key(title):
        return
    names = aliases()
    names[_name_key(said)] = title
    try:
        ALIASES.write_text(json.dumps(dict(list(names.items())[-200:]), ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def forget(said: str) -> None:
    names = aliases()
    if names.pop(_name_key(said), None) is not None:
        try:
            ALIASES.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


def search_terms(query: str) -> list[str]:
    """What to type into WhatsApp's search: the name as you said it, then its most telling single words. WhatsApp
    only finds names containing the typed text as-is, so "omi shotu" finds nothing for "Omi Bro Shotu Bangalore",
    but "shotu" does (longer words first: they're rarer than "omi")."""
    terms = [query.strip()]
    for w in sorted(dict.fromkeys(_name_words(query)), key=len, reverse=True):
        if len(w) >= 3 and w != _name_key(query):
            terms.append(w)
    # heard in Hindi and spelled back by sound ("raahul", "shotoo"): WhatsApp has "Rahul", "Shotu"
    if (short := _short(_name_key(query))) != _name_key(query):
        terms.insert(1, short)
    return terms[:4]


def _same_text(a: str, b: str) -> bool:
    a, b = " ".join((a or "").split()), " ".join((b or "").split())
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


# ---------------------------------------------------------------- keyboard and mouse

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_VK = {"enter": 0x0D, "esc": 0x1B, "down": 0x28, "up": 0x26, "back": 0x08, "ctrl": 0x11, "shift": 0x10,
       "alt": 0x12, "a": 0x41}
_KEYUP, _UNICODE = 0x0002, 0x0004


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    i = _INPUT()
    i.type = 1
    i.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)
    return i


def _send_input(inputs: list[_INPUT]) -> None:
    """Keyboard events to Windows. (Named _send until 2026-09-24, when the message-sending _send further down
    replaced it and every key press failed with "_send() missing 2 required positional arguments".)"""
    arr = (_INPUT * len(inputs))(*inputs)
    _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


_front = 0  # the WhatsApp window; every key press and click checks it is still in front
_abort = threading.Event()  # set when the turn is cancelled: stops before the next key press


def _guard() -> None:
    if _abort.is_set() or policy.halted:
        raise _Fail("stopped")
    if _front and _user32.GetForegroundWindow() != _front:
        raise _Fail("you switched to another window, so I stopped typing")


def _press(*names: str) -> None:
    """A key or a chord ("ctrl", "a"): pressed in order, released in reverse."""
    _guard()
    codes = [_VK[n] for n in names]
    _send_input([_key(c) for c in codes] + [_key(c, flags=_KEYUP) for c in reversed(codes)])


def _type(text: str, delay: float = 0.02) -> None:
    """Type any text (Hindi, emoji) one character at a time: WhatsApp drops characters sent in one burst.
    A new line is Shift+Enter, so it never sends early."""
    for n, line in enumerate(text.split("\n")):
        if n:
            _press("shift", "enter")
        raw = line.encode("utf-16-le")
        units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
        i = 0
        while i < len(units):
            _guard()
            size = 2 if 0xD800 <= units[i] < 0xDC00 else 1  # an emoji is two UTF-16 units: send them together
            _send_input([k for u in units[i:i + size] for k in (_key(scan=u, flags=_UNICODE), _key(scan=u, flags=_UNICODE | _KEYUP))])
            i += size
            time.sleep(delay)


def _click(x: int, y: int) -> None:
    """Click a point on screen, then put the mouse back where it was."""
    _guard()
    old = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(old))
    _user32.SetCursorPos(x, y)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)
    _user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.05)
    _user32.SetCursorPos(old.x, old.y)


# ---------------------------------------------------------------- the WhatsApp window

def installed() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "whatsapp"))
        return True
    except OSError:
        return False


def running() -> bool:
    return any((p.info.get("name") or "").lower().startswith("whatsapp") for p in psutil.process_iter(["name"]))


def status() -> dict:
    return {"installed": installed(), "running": running()}


def _proc_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return ""


def _window() -> int:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            _user32.GetClassNameW(hwnd, cls, 64)
            if cls.value == "WinUIDesktopWin32WindowClass" and _proc_name(hwnd).startswith("whatsapp"):
                found.append(hwnd)
        return True

    _user32.EnumWindows(each, 0)
    return found[0] if found else 0


def _to_front(hwnd: int) -> bool:
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    for _ in range(4):
        if _user32.GetForegroundWindow() == hwnd:
            return True
        # a key event lets a background process hand the foreground over (not guarded: WhatsApp isn't in front yet)
        _send_input([_key(_VK["alt"]), _key(_VK["alt"], flags=_KEYUP)])
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    return _user32.GetForegroundWindow() == hwnd


def _open(link: str = "whatsapp:", timeout: float = 20.0) -> int:
    global _front
    _front = 0
    hwnd = _window()
    if not hwnd or link != "whatsapp:":
        os.startfile(link)
        end = time.monotonic() + timeout
        while time.monotonic() < end and not (hwnd := _window()):
            time.sleep(0.2)
    if not hwnd:
        raise _Fail("WhatsApp didn't open")
    if not _to_front(hwnd):
        raise _Fail("WhatsApp didn't come to the front")
    _front = hwnd
    return hwnd


class _Page:
    """The WhatsApp page inside the window, read through Windows UI Automation (the screen-reader interface)."""

    def __init__(self, hwnd: int) -> None:
        from pywinauto.uia_defines import IUIA  # loaded on the WhatsApp thread only

        uia = IUIA()
        self.u, self.U = uia.iuia, uia.UIA_dll
        self.hwnd = hwnd
        self.root = self.u.ElementFromHandle(hwnd)
        self.walker = self.u.ControlViewWalker
        self.chat_left = 0  # left edge of the chat area (right of the chat list), known once the search box is found

    def _is(self, control_type: int):
        return self.u.CreatePropertyCondition(self.U.UIA_ControlTypePropertyId, control_type)

    @staticmethod
    def _wait(get, timeout: float, every: float = 0.1):
        """Poll until get() returns something (an empty COM pointer counts as nothing). None on timeout."""
        end = time.monotonic() + timeout
        while True:
            try:
                got = get()
            except Exception:  # elements come and go while WhatsApp redraws
                got = None
            if got:
                return got
            if time.monotonic() >= end:
                return None
            time.sleep(every)

    def search_box(self):
        # The chat list's search box is the window's first text box. Searching from the window, not from a page
        # element: WhatsApp's window holds a second, empty copy of the page that has no text boxes.
        box = self._wait(lambda: self.root.FindFirst(self.U.TreeScope_Descendants, self._is(self.U.UIA_EditControlTypeId)), 15)
        if box is not None:
            self.chat_left = box.CurrentBoundingRectangle.right + int(16 * self.scale())
        return box

    def scale(self) -> float:
        return (_user32.GetDpiForWindow(self.hwnd) or 96) / 96

    def value(self, el) -> str:
        return el.GetCurrentPropertyValue(self.U.UIA_ValueValuePropertyId) or ""

    def has_value(self, el) -> bool:
        return bool(el.GetCurrentPropertyValue(self.U.UIA_IsValuePatternAvailablePropertyId))

    def has_focus(self, el) -> bool:
        try:
            return bool(el.CurrentHasKeyboardFocus)
        except Exception:
            return False

    def composer(self):
        """The open chat's message box, found by hit-testing where it sits (bottom of the chat area): one quick
        call, where searching the page for it takes 10-20 s."""
        wr = wintypes.RECT()
        _user32.GetWindowRect(self.hwnd, ctypes.byref(wr))
        left = self.chat_left or wr.left + (wr.right - wr.left) // 3
        x = (left + wr.right) // 2
        for dy in (38, 30, 48, 58):
            el = self.u.ElementFromPoint(self.U.tagPOINT(x, int(wr.bottom - dy * self.scale())))
            for _ in range(6):  # the hit may be text inside the box: climb to the box itself
                if not el:
                    break
                if el.CurrentControlType == self.U.UIA_EditControlTypeId and (el.CurrentName or "").startswith(COMPOSER):
                    return el
                el = self.walker.GetParentElement(el)
        return None

    def click(self, el) -> None:
        r = el.CurrentBoundingRectangle
        _click((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def results_grid(self, box):
        """WhatsApp's "Search results." list under the search box, found by hit-testing just below it (one quick call
        per point, like the message box). None until the results show."""
        r = box.CurrentBoundingRectangle
        x = (r.left + r.right) // 2
        for dy in (90, 120, 150, 200, 260):
            el = self.u.ElementFromPoint(self.U.tagPOINT(x, r.bottom + int(dy * self.scale())))
            for _ in range(8):
                if not el:
                    break
                if el.CurrentControlType == self.U.UIA_DataGridControlTypeId:
                    return el
                el = self.walker.GetParentElement(el)
        return None

    def result_rows(self, box) -> list[tuple[str, object]] | None:
        """The chats and contacts WhatsApp lists for what's in the search box, read off the screen without opening
        any: [(chat name, row)]. Message hits (the "Messages" section) are skipped. None if the list can't be read.
        Measured 2026-09-25 (WhatsApp 2.26xx): a DataGrid whose rows are named like "Omi Bro Shotu Bangalore 2:56 pm
        You deleted this message", with section rows named "Chats", "Contacts", "Messages"."""
        grid = self._wait(lambda: self.results_grid(box), 2.5)
        if grid is None:
            return None
        rows: list[tuple[str, object]] = []
        section = "chats"
        el = self.walker.GetFirstChildElement(grid)
        while el and len(rows) < 12:
            if el.CurrentControlType == self.U.UIA_DataItemControlTypeId:
                name = " ".join((el.CurrentName or "").split())
                if name.casefold() in _SECTIONS:
                    section = name.casefold()
                elif section != "messages" and (title := _ROW_END.sub("", name).strip()):
                    rows.append((title, el))
            el = self.walker.GetNextSiblingElement(el)
        return rows


# ---------------------------------------------------------------- sending

def _type_and_send(page: _Page, composer, message: str, dry_run: bool) -> None:
    if page.has_value(composer) and page.value(composer).strip():
        raise _Fail("that chat has an unsent draft, so I left it alone")
    if not page.has_focus(composer):
        page.click(composer)
        time.sleep(0.2)
    _type(message, 0.015)
    time.sleep(0.25)
    if page.has_value(composer) and not _same_text(page.value(composer), message):
        _press("ctrl", "a")
        _press("back")
        raise _Fail("the message didn't type correctly, so I cleared it")
    if dry_run:
        _press("ctrl", "a")
        _press("back")
        return
    _press("enter")
    if page.has_value(composer) and page._wait(lambda: not page.value(composer).strip(), 1.5) is None:
        raise _Fail("I pressed send but WhatsApp still shows the message")


def _type_search(page: _Page, box, query: str) -> None:
    """Type `query` into WhatsApp's search box (replacing what's there) and let the results settle."""
    page.click(box)
    _press("ctrl", "a")
    _press("back")
    _type(query, 0.03)
    if page._wait(lambda: page.value(box).strip().casefold() == query.strip().casefold(), 1.5) is None:
        raise _Fail("WhatsApp's search didn't take the name")
    time.sleep(0.9)  # results appear as you type


def _open_result(page: _Page, box, query: str, pick: int):
    """Search WhatsApp for `query` and open result number `pick` (0 = the first). Returns the chat's message box."""
    _type_search(page, box, query)
    for _ in range(pick + 1):
        _press("down")
        time.sleep(0.12)
    _press("enter")
    return page._wait(page.composer, 3.0)


class _Ambiguous(Exception):
    """More than one chat fits the name you said (two Rahuls): PLAG asks which one instead of guessing."""

    def __init__(self, options: list[str]):
        super().__init__("more than one match")
        self.options = options


def _name_words(text: str) -> list[str]:
    return [w for w in _words(text) if w not in _HONORIFICS] or _words(text)


def exact(query: str, title: str) -> bool:
    """You said the chat's whole name ("Rahul Sharma", or "Rahul" for "Rahul Bhai"), not just part of it."""
    return _name_words(query) == _name_words(title)


def _scan(page: _Page, box, term: str, query: str, tried: list[str]):
    """Search WhatsApp for `term` and open the result that is `query`'s chat: (message box, chat name), or None. If
    only part of the name was said and the next result is a different person who also fits, raises _Ambiguous."""
    first: tuple[int, str] | None = None
    seen: list[str] = []
    for pick in range(4):  # the first result is usually right; a group or a message hit can come first
        composer = _open_result(page, box, term, pick)
        if composer is None:
            break
        title = composer.CurrentName[len(COMPOSER):].strip()
        if title in seen:
            break  # the same chat again: the results ran out (or there were none and the old chat is still open)
        seen.append(title)
        ok = fits(query, title)
        if first is not None:
            if ok and title != first[1]:
                raise _Ambiguous([first[1], title])
            break  # the result after a partial match is someone else: the partial match is the only one
        if ok and exact(query, title):
            return composer, title
        if ok:
            first = (pick, title)
        elif title not in tried:
            tried.append(title)
    if first is not None:
        composer = _open_result(page, box, term, first[0])  # back to the one match
        if composer is not None and composer.CurrentName[len(COMPOSER):].strip() == first[1]:
            return composer, first[1]
        raise _Fail("WhatsApp's search results changed, so I stopped")
    return None


_UNREADABLE = object()  # the results list couldn't be read: fall back to opening results one by one


def _search_once(page: _Page, box, term: str, query: str, tried: list[str]):
    """Type `term` once, read the results off the screen, and open only the chat that is `query`'s:
    (message box, chat name), None when no result is that person, or _UNREADABLE. Two different people who both
    fit (and neither exactly) raise _Ambiguous, before anything is opened."""
    _type_search(page, box, term)
    rows = page.result_rows(box)
    if rows is None:
        return _UNREADABLE
    # the list re-draws while results stream in (2026-09-25: a click on a row just replaced opened nothing): read it
    # until two looks agree, and click from the fresh one
    for _ in range(3):
        time.sleep(0.3)
        again = page.result_rows(box) or []
        stable = [t for t, _ in again] == [t for t, _ in rows]
        rows = again
        if stable:
            break
    fitting = [(t, r) for t, r in rows if fits(query, t)]
    tried.extend(t for t, _ in rows if t not in tried and (t, _) not in fitting)
    if not fitting:
        return None
    exact_ = [(t, r) for t, r in fitting if exact(query, t)]
    names = list(dict.fromkeys(t for t, _ in fitting))
    if not exact_ and len(names) > 1:
        raise _Ambiguous(names[:3])
    title, row = (exact_ or fitting)[0]
    composer = None
    for _ in range(2):  # a second click on a freshly read row, if the first landed as the list re-drew
        page.click(row)
        composer = page._wait(page.composer, 2.0)
        if composer is not None:
            break
        row = next((r for t, r in (page.result_rows(box) or []) if t == title), None)
        if row is None:
            break
    if composer is None:
        raise _Fail(f"WhatsApp didn't open {title}'s chat")
    opened = composer.CurrentName[len(COMPOSER):].strip()
    if opened != title and not fits(query, opened):
        raise _Fail(f"WhatsApp opened {opened} instead of {title}, so I stopped")
    return composer, opened


def _find_chat(page: _Page, query: str):
    """Open the chat for `query`; returns (message box, chat name). The name is typed ONCE and the results are read off
    the screen (2026-09-25: opening results one by one to check them meant typing it 3-4 times and opening the wrong
    chats on the way). A name that worked before goes straight to its chat; otherwise the name as said, then its single
    words, until a chat fits every word you said."""
    box = page.search_box()
    if box is None:
        raise _Fail("WhatsApp's search box isn't showing")

    def attempt(term: str, want: str, tried: list[str]):
        found = _search_once(page, box, term, want, tried)
        return _scan(page, box, term, want, tried) if found is _UNREADABLE else found

    known = aliases().get(_name_key(query))
    if known:
        try:
            found = attempt(known, known, [])
        except _Ambiguous:
            found = None
        if found:
            return found
        forget(query)  # renamed or gone: search afresh
    tried: list[str] = []
    for term in search_terms(query):
        found = attempt(term, query, tried)
        if found:
            remember(query, found[1])
            return found
    closest = f" The closest chats were {', '.join(tried[:4])}." if tried else ""
    raise _Fail(f"I couldn't find {query} in your WhatsApp.{closest} Nothing was sent")


def _open_number(page: _Page, phone: str, text: str = ""):
    """A chat by phone number, through WhatsApp's click-to-chat link; returns (message box, chat name)."""
    link = f"whatsapp://send?phone={phone}" + (f"&text={urllib.parse.quote(text)}" if text else "")
    os.startfile(link)
    page.search_box()  # locates the chat area
    composer = page._wait(page.composer, 12.0)
    if composer is None:
        raise _Fail("WhatsApp didn't open a chat for that number. Is it on WhatsApp?")
    return composer, composer.CurrentName[len(COMPOSER):].strip()


def _send(to: str, message: str, dry_run: bool) -> str:
    page = _Page(_open())
    phone = _as_phone(to)
    if phone:
        composer, title = _open_number(page, phone, message)  # WhatsApp fills the message in itself
        if page.has_value(composer) and not _same_text(page.value(composer), message):
            raise _Fail("WhatsApp didn't fill in the message, so nothing was sent")
        if dry_run:
            _press("ctrl", "a")
            _press("back")
        else:
            _press("enter")
        return title
    composer, title = _find_chat(page, to)
    _type_and_send(page, composer, message, dry_run)
    return title


def _open_chat(to: str) -> str:
    """Find the chat and leave it open, the message box ready; nothing is typed."""
    page = _Page(_open())
    phone = _as_phone(to)
    _composer, title = _open_number(page, phone) if phone else _find_chat(page, to)
    return title


def _visible_windows() -> int:
    count = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        nonlocal count
        if _user32.IsWindowVisible(hwnd) and _proc_name(hwnd).startswith("whatsapp"):
            count += 1
        return True

    _user32.EnumWindows(each, 0)
    return count


def _call(to: str, video: bool, dry_run: bool) -> str:
    page = _Page(_open())
    phone = _as_phone(to)
    _composer, title = _open_number(page, phone) if phone else _find_chat(page, to)
    label = "Video call" if video else "Voice call"
    want = page.u.CreateAndCondition(page._is(page.U.UIA_ButtonControlTypeId),
                                     page.u.CreatePropertyCondition(page.U.UIA_NamePropertyId, label))
    button = page._wait(lambda: page.root.FindFirst(page.U.TreeScope_Descendants, want), 5.0)
    if button is None:
        raise _Fail(f"WhatsApp doesn't show a {label.lower()} button for {title}")
    if dry_run:
        return title
    before = _visible_windows()
    page.click(button)
    # WhatsApp opens the call in its own window; if none appears, say so rather than claim a call
    if page._wait(lambda: _visible_windows() > before, 5.0) is None:
        raise _Fail(f"I pressed {label.lower()} for {title}, but no call window appeared. Check WhatsApp")
    return title


def _run(fn, *args) -> str:
    global _front
    _abort.clear()
    _user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # screen positions match what UI Automation reports
    try:
        return fn(*args)
    finally:
        _front = 0


def _init_thread() -> None:
    import comtypes
    comtypes.CoInitialize()


_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whatsapp", initializer=_init_thread)


async def _drive(fn, to: str, *args) -> dict:
    try:
        name = await asyncio.get_running_loop().run_in_executor(_worker, _run, fn, to, *args)
    except _Ambiguous as e:
        return {"ok": False, "detail": "more than one match", "name": to, "options": e.options}
    except _Fail as e:
        return {"ok": False, "detail": str(e), "name": to, "options": []}
    except asyncio.CancelledError:
        _abort.set()  # the WhatsApp thread stops before its next key press
        raise
    return {"ok": True, "detail": "done", "name": name, "options": []}


async def send(to: str, message: str, *, dry_run: bool = False) -> dict:
    """Send `message` to the WhatsApp chat named `to` (or a phone number).
    Returns {ok, detail, name, options}; `options` lists the chats to choose from when the name fits two people.
    dry_run does everything except pressing Enter, and clears what it typed."""
    if not installed():
        phone = _as_phone(to)
        if phone:
            await asyncio.to_thread(os.startfile, f"https://wa.me/{phone}?text={urllib.parse.quote(message)}")
            return {"ok": False, "detail": "WhatsApp Desktop isn't installed; I opened the chat in your browser, press send there",
                    "name": to, "options": []}
        return {"ok": False, "detail": "WhatsApp Desktop isn't installed", "name": to, "options": []}
    result = await _drive(_send, to, message, dry_run)
    if result["ok"]:
        result["detail"] = "typed but not sent (dry run)" if dry_run else "sent"
    return result


async def open_chat(to: str) -> dict:
    """Open the WhatsApp chat of `to` (a chat name or phone number) and verify it's the right one."""
    if not installed():
        return {"ok": False, "detail": "WhatsApp Desktop isn't installed", "name": to, "options": []}
    result = await _drive(_open_chat, to)
    if result["ok"]:
        result["detail"] = f"chat open: {result['name']}"
    return result


async def call(to: str, *, video: bool = False, dry_run: bool = False) -> dict:
    """Start a WhatsApp voice or video call with `to` (a chat name or phone number) from WhatsApp Desktop.
    dry_run finds the chat and the call button but doesn't press it."""
    if not installed():
        return {"ok": False, "detail": "WhatsApp Desktop isn't installed", "name": to, "options": []}
    result = await _drive(_call, to, video, dry_run)
    if result["ok"]:
        result["detail"] = "found the call button (dry run)" if dry_run else "calling"
    return result
