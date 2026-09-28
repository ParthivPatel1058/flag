"""PLAG using your laptop the way you do: reading what's on screen and clicking, typing and pressing keys.

It reads windows through **UI Automation**, the same interface a screen reader uses, so it works with the real
buttons and boxes of an app by their names ("Save", "File name") instead of guessing pixel positions. That is far
more accurate than looking at a picture of the screen, and it's the way PLAG already drives WhatsApp Desktop.

What it will never do, whatever it's asked:
- run in an app on the blocked list (terminals, the registry editor, Windows Settings, Credential Manager...)
- type into a password box (UI Automation marks them, and PLAG refuses)
- press a risky button (delete, uninstall, pay, send, shut down...) without your "yes" on an approval card
- carry on when you take the mouse back, switch windows, press Esc, or hit Halt

Everything on screen is untrusted: text in a window is something PLAG reads, never an instruction it follows.
"""

import ctypes
import re
import time
from ctypes import wintypes

import psutil

from .policy import policy
from .whatsapp import _KEYUP, _UNICODE, _key, _send_input  # the keyboard primitives, proven on WhatsApp

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# Apps PLAG will not drive: a mistake in these can break Windows or hand over your secrets.
BLOCKED_APPS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe", "regedit.exe",
    "mmc.exe", "taskmgr.exe", "systemsettings.exe", "control.exe", "rundll32.exe", "msconfig.exe", "diskmgmt.exe",
    "gpedit.exe", "secpol.exe", "certmgr.exe", "lusrmgr.exe", "bitlockerwizard.exe", "keepass.exe", "keepassxc.exe",
    "1password.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe", "python.exe", "py.exe", "installer.exe",
}
# Window titles PLAG will not drive either (a password or security dialog can appear inside any app).
# The trailing \b means every alternative must end on a word boundary, so stems and plurals need spelling out:
# "authenticat" alone could never match "Authentication", and "password" missed "Passwords".
BLOCKED_TITLES = re.compile(r"\b(?:sign[- ]?in|log ?in|passwords?|passcodes?|credentials?|authenticat\w*|"
                            r"verify your identity|two[- ]factor|security keys?|windows security|"
                            r"user account control|uac|net ?banking|payments?|card details|cvv|upi pin)\b", re.I)
# Buttons and menu items that need your "yes" before PLAG presses them.
RISKY = re.compile(r"\b(?:delete|remove|uninstall|format|erase|wipe|discard|permanently|empty (?:the )?recycle|"
                   r"pay|buy|purchase|order|checkout|place order|subscribe|send|publish|post|tweet|share|"
                   r"shut ?down|restart|sign out|log ?out|reset|factory|overwrite|replace|move to trash|"
                   r"don'?t save|close without saving)\b", re.I)

MAX_ELEMENTS = 60  # what PLAG shows the AI: enough to work with, small enough to stay fast
_MOUSE_LEFT_DOWN, _MOUSE_LEFT_UP, _MOUSE_WHEEL = 0x0002, 0x0004, 0x0800
_VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "back": 0x08, "delete": 0x2E, "space": 0x20, "home": 0x24,
       "end": 0x23, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "pageup": 0x21, "pagedown": 0x22,
       "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "f2": 0x71, "f5": 0x74}
_VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})


class ComputerError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class Blocked(ComputerError):
    """PLAG refuses: a blocked app or window, or a password box."""


class NeedsYes(ComputerError):
    """A risky button: it waits for your approval."""
    def __init__(self, message: str, label: str):
        super().__init__(message, "needs_yes")
        self.label = label


# ---------------------------------------------------------------- the window PLAG is working in

_front = 0        # the window PLAG is driving; every action checks it is still in front
_stopped = False  # you pressed Esc or took over


def stop() -> None:
    global _stopped
    _stopped = True


def begin(hwnd: int) -> None:
    global _front, _stopped
    _front, _stopped = hwnd, False


def _guard() -> None:
    if _stopped or policy.halted:
        raise ComputerError("Stopped.", "stopped")
    if _front and _user32.GetForegroundWindow() != _front:
        raise ComputerError("You switched to another window, so I stopped.", "you_took_over")


def _proc_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return ""


def _title(hwnd: int) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def windows() -> list[dict]:
    """The open windows PLAG may work in: [{hwnd, title, app}] (blocked apps and dialogs are left out)."""
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _title(hwnd)
        app = _proc_name(hwnd)
        if title and app and app not in BLOCKED_APPS and not BLOCKED_TITLES.search(title):
            found.append({"hwnd": hwnd, "title": title[:120], "app": app})
        return True

    _user32.EnumWindows(each, 0)
    return found


def check_allowed(hwnd: int, allowed: set[str] | None = None) -> tuple[str, str]:
    """The app and title, or Blocked. `allowed` is your list from Settings (empty = every app but the blocked ones)."""
    app, title = _proc_name(hwnd), _title(hwnd)
    if not app:
        raise Blocked("I can't tell which app that window belongs to.", "unknown_app")
    if app in BLOCKED_APPS:
        raise Blocked(f"I don't drive {app}: it can change Windows itself or hold your passwords.", "blocked_app")
    if BLOCKED_TITLES.search(title):
        raise Blocked("That looks like a sign-in or payment window, so I won't touch it.", "blocked_window")
    if allowed and app not in allowed:
        raise Blocked(f"{app} isn't in your allowed apps (⚙ Settings → Computer use).", "not_allowed")
    return app, title


def focus(hwnd: int, timeout: float = 4.0) -> bool:
    _user32.SetForegroundWindow(hwnd)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------- reading what's on screen

class Screen:
    """One window read through UI Automation: its elements, numbered so the AI can name one to act on."""

    def __init__(self, hwnd: int) -> None:
        from pywinauto.uia_defines import IUIA  # loaded on first use (COM), like whatsapp.py

        uia = IUIA()
        self.u, self.U = uia.iuia, uia.UIA_dll
        self.hwnd = hwnd
        self.root = self.u.ElementFromHandle(hwnd)
        self.walker = self.u.ControlViewWalker
        self.items: list[dict] = []
        self._els: list[object] = []

    def _kind(self, ct: int) -> str:
        U = self.U
        return {U.UIA_ButtonControlTypeId: "button", U.UIA_EditControlTypeId: "box", U.UIA_CheckBoxControlTypeId: "checkbox",
                U.UIA_ComboBoxControlTypeId: "dropdown", U.UIA_ListItemControlTypeId: "item",
                U.UIA_MenuItemControlTypeId: "menu", U.UIA_RadioButtonControlTypeId: "radio",
                U.UIA_TabItemControlTypeId: "tab", U.UIA_HyperlinkControlTypeId: "link",
                U.UIA_TreeItemControlTypeId: "tree item", U.UIA_SplitButtonControlTypeId: "button",
                U.UIA_DocumentControlTypeId: "document", U.UIA_TextControlTypeId: "text"}.get(ct, "")

    def read(self, limit: int = MAX_ELEMENTS) -> list[dict]:
        """Walk the window and list what can be acted on, plus a little text for context."""
        U, out, els = self.U, [], []
        stack = [(self.root, 0)]
        texts = 0
        while stack and len(out) < limit:
            el, depth = stack.pop()
            if depth > 14:
                continue
            try:
                child = self.walker.GetFirstChildElement(el)
                kids = []
                while child and len(kids) < 60:
                    kids.append((child, depth + 1))
                    child = self.walker.GetNextSiblingElement(child)
                stack.extend(reversed(kids))
                if el is self.root:
                    continue
                if el.CurrentIsOffscreen:
                    continue
                kind = self._kind(el.CurrentControlType)
                if not kind:
                    continue
                name = (el.CurrentName or "").strip()
                value = ""
                if el.GetCurrentPropertyValue(U.UIA_IsValuePatternAvailablePropertyId):
                    value = str(el.GetCurrentPropertyValue(U.UIA_ValueValuePropertyId) or "")
                secret = bool(el.GetCurrentPropertyValue(U.UIA_IsPasswordPropertyId))
                if kind == "text":  # text is only context: keep a few, never act on them
                    if texts >= 12 or not name or len(name) < 2:
                        continue
                    texts += 1
                if not name and not value:
                    continue
            except Exception:  # elements come and go while an app redraws
                continue
            out.append({"n": len(out) + 1, "kind": kind, "name": name[:90], "value": ("••••" if secret else value[:90]),
                        "enabled": bool(el.CurrentIsEnabled), "secret": secret})
            els.append(el)
        self.items, self._els = out, els
        return out

    def element(self, n: int):
        if not 1 <= n <= len(self._els):
            raise ComputerError(f"There's no [{n}] on screen now.", "no_such_element")
        return self._els[n - 1], self.items[n - 1]

    def as_text(self) -> str:
        """The screen for the AI: a numbered list it can point at."""
        lines = []
        for it in self.items:
            bit = f"[{it['n']}] {it['kind']} \"{it['name']}\"" if it["name"] else f"[{it['n']}] {it['kind']}"
            if it["value"]:
                bit += f" = \"{it['value']}\""
            if not it["enabled"]:
                bit += " (greyed out)"
            lines.append(bit)
        return "\n".join(lines) or "(nothing readable on this window)"


# ---------------------------------------------------------------- doing things

def click(screen: Screen, n: int, approved: bool = False) -> str:
    """Click element `n`. A risky button raises NeedsYes until you approve it."""
    _guard()
    el, item = screen.element(n)
    if not item["enabled"]:
        raise ComputerError(f"“{item['name']}” is greyed out, so it can't be clicked.", "disabled")
    label = item["name"] or item["kind"]
    if RISKY.search(label) and not approved:
        raise NeedsYes(f"Press “{label}”?", label)
    r = el.CurrentBoundingRectangle
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    old = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(old))
    _user32.SetCursorPos(x, y)
    _user32.mouse_event(_MOUSE_LEFT_DOWN, 0, 0, 0, 0)
    _user32.mouse_event(_MOUSE_LEFT_UP, 0, 0, 0, 0)
    time.sleep(0.05)
    _user32.SetCursorPos(old.x, old.y)
    return f"clicked {item['kind']} “{label}”"


def type_text(screen: Screen, text: str, into: int | None = None, clear: bool = False) -> str:
    """Type into the box that has focus, or into element `into` after clicking it. Password boxes are refused."""
    _guard()
    where = ""
    if into is not None:
        _el, item = screen.element(into)
        if item["secret"]:
            raise Blocked("That's a password box. I never type into those: please type it yourself.", "password_box")
        if item["kind"] not in ("box", "document", "dropdown"):
            # clicking to focus skips the risky-button check, so it may only ever land on something you type into
            raise ComputerError(f"“{item['name'] or item['kind']}” isn't a box I can type into.", "not_a_box")
        click(screen, into, approved=True)  # focusing a text box is never the risky part
        time.sleep(0.15)
        where = f" into “{item['name'] or item['kind']}”"
    if clear:
        _press("ctrl", "a")
        _press("delete")
    for line in text.split("\n")[:40]:
        raw = line.encode("utf-16-le")
        units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
        i = 0
        while i < len(units):
            _guard()
            size = 2 if 0xD800 <= units[i] < 0xDC00 else 1  # an emoji is two UTF-16 units
            _send_input([k for u in units[i:i + size]
                         for k in (_key(scan=u, flags=_UNICODE), _key(scan=u, flags=_UNICODE | _KEYUP))])
            i += size
            time.sleep(0.012)
    return f"typed {len(text)} characters{where}"


def _press(*names: str) -> None:
    _guard()
    codes = [_VK[n] for n in names if n in _VK]
    if not codes:
        raise ComputerError("I don't know that key.", "no_such_key")
    _send_input([_key(c) for c in codes] + [_key(c, flags=_KEYUP) for c in reversed(codes)])


def press(chord: str, approved: bool = False) -> str:
    """A key or a chord: "enter", "ctrl+s", "alt+tab"."""
    names = [p.strip().lower() for p in chord.replace(" ", "+").split("+") if p.strip()]
    if not names:
        raise ComputerError("No key given.", "no_key")
    unknown = [n for n in names if n not in _VK]
    if unknown:
        raise ComputerError(f"I don't know the key “{unknown[0]}”.", "no_such_key")
    if names == ["delete"] and not approved:
        raise NeedsYes("Press Delete?", "Delete")
    _press(*names)
    return f"pressed {'+'.join(names)}"


def scroll(amount: int = -3) -> str:
    """Scroll the window under the mouse (negative = down)."""
    _guard()
    _user32.mouse_event(_MOUSE_WHEEL, 0, 0, int(amount) * 120, 0)
    time.sleep(0.05)
    return f"scrolled {'down' if amount < 0 else 'up'}"
