"""Your files: open a file or folder by name from your Desktop, Downloads, Documents, Pictures, Videos and Music
("open my resume from the desktop", "open the physics notes pdf", "open Downloads", "what's on my desktop").

How it finds them, measured 2026-09-24 on this laptop: a direct scan of those folders (4 levels deep, skipping code
folders like node_modules) lists ~3,900 items in ~0.5 s and is kept for a minute; names are matched word by word,
then fuzzily ("resum" finds "Resume_2026.pdf"), newest first. If nothing matches there, Windows' own search index is
asked (80-300 ms warm; its first query of the day took 11 s, so it gets a 6 s limit).
Opening uses Windows' default app for the file. Scripts and installers (.bat, .ps1, .msi…) are never run by voice:
PLAG shows them in Explorer instead, so a misheard word can't run a program.
"""

import ctypes
import difflib
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
_GUIDS = {  # Windows' known folders (they may live in OneDrive)
    "desktop": (0xB4BFCC3A, 0xDB2C, 0x424C, (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41)),
    "downloads": (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B)),
    "documents": (0xFDD39AD0, 0x238F, 0x46AF, (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7)),
    "pictures": (0x33E28130, 0x4E1E, 0x4676, (0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB)),
    "videos": (0x18989B1D, 0x99B5, 0x455B, (0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC)),
    "music": (0x4BD8D571, 0x6D19, 0x48D3, (0xBE, 0x97, 0x42, 0x22, 0x20, 0x08, 0x0E, 0x43)),
}
ALIASES = {"desktop": "desktop", "download": "downloads", "downloads": "downloads", "documents": "documents",
           "document": "documents", "docs": "documents", "my documents": "documents", "pictures": "pictures",
           "photos": "pictures", "images": "pictures", "videos": "videos", "video": "videos", "movies": "videos",
           "music": "music", "songs": "music", "डेस्कटॉप": "desktop", "डाउनलोड": "downloads"}
SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages", "AppData", "dist", "build", ".next",
        ".cache", "$RECYCLE.BIN"}
NEVER_RUN = {".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".reg",
             ".scr", ".hta", ".cpl", ".com", ".pif", ".jar"}
KINDS = {  # "open the pdf about X" -> extensions
    "pdf": {".pdf"}, "word": {".doc", ".docx"}, "document": {".doc", ".docx", ".pdf", ".txt", ".odt", ".rtf"},
    "excel": {".xls", ".xlsx", ".csv"}, "sheet": {".xls", ".xlsx", ".csv"}, "spreadsheet": {".xls", ".xlsx", ".csv"},
    "ppt": {".ppt", ".pptx"}, "presentation": {".ppt", ".pptx"}, "powerpoint": {".ppt", ".pptx"},
    "photo": {".jpg", ".jpeg", ".png", ".heic", ".webp"}, "picture": {".jpg", ".jpeg", ".png", ".heic", ".webp"},
    "image": {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}, "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "song": {".mp3", ".wav", ".m4a", ".flac"}, "audio": {".mp3", ".wav", ".m4a", ".flac"}, "text": {".txt", ".md"},
    "zip": {".zip", ".rar", ".7z"}, "notes": {".txt", ".md", ".docx", ".pdf"},
}
_scan: tuple[float, list[dict]] | None = None
_lock = threading.Lock()


class FileError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def known_folder(name: str) -> Path | None:
    key = ALIASES.get((name or "").casefold().strip(), (name or "").casefold().strip())
    g = _GUIDS.get(key)
    if not g:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
    out = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(GUID(g[0], g[1], g[2], (ctypes.c_ubyte * 8)(*g[3]))), 0, None,
                                                      ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except (AttributeError, OSError):
        pass
    fallback = HOME / key.capitalize()
    return fallback if fallback.exists() else None


def _roots(only: str | None = None) -> list[Path]:
    names = [only] if only else list(_GUIDS)
    out: list[Path] = []
    for n in names:
        p = known_folder(n)
        if p and p.exists() and p not in out:
            out.append(p)
    return out


def _walk(root: Path, depth: int, out: list[dict]) -> None:
    try:
        with os.scandir(root) as it:
            for e in it:
                if e.name.startswith((".", "~$")) or e.name in SKIP:
                    continue
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    out.append({"path": e.path, "name": e.name, "dir": is_dir, "mtime": e.stat(follow_symlinks=False).st_mtime})
                except OSError:
                    continue
                if is_dir and depth < 4 and len(out) < 60_000:
                    _walk(Path(e.path), depth + 1, out)
    except OSError:
        pass


def entries(fresh: bool = False) -> list[dict]:
    """Everything in your main folders (kept for a minute)."""
    global _scan
    with _lock:
        if _scan and not fresh and time.monotonic() - _scan[0] < 60:
            return _scan[1]
        out: list[dict] = []
        for r in _roots():
            _walk(r, 0, out)
        _scan = (time.monotonic(), out)
        return out


_FILLER = re.compile(r"\b(?:the|my|a|an|file|files|folder|document|called|named|from|on|in|of|wala|wali|vala|ki|ka|ke)\b", re.I)


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\W_]+", _FILLER.sub(" ", text.casefold())) if len(w) > 1]


def _score(want: list[str], name: str, parent: str = "") -> float:
    """How well the spoken words fit a file name; the folder it's in counts too ("PLAG reports" = PLAG\\Reports)."""
    stem = Path(name).stem.casefold() if "." in name[1:] else name.casefold()
    have = [w for w in re.split(r"[\W_]+", stem) if w]
    have += [w for w in re.split(r"[\W_]+", parent.casefold()) if w and w not in have]
    if not want or not have:
        return 0.0
    if " ".join(want) == " ".join(have) or "".join(want) == "".join(have):
        return 3.0
    hits = 0.0
    for w in want:
        if any(h == w for h in have):
            hits += 1
        elif any(h.startswith(w) or w.startswith(h) and len(h) > 2 for h in have):
            hits += 0.8
        else:
            best = max(difflib.SequenceMatcher(None, w, h).ratio() for h in have)
            hits += best if best >= 0.78 else 0
    return 2.0 * hits / len(want) if hits >= len(want) * 0.75 else 0.0


def _index_search(words: list[str], limit: int = 20) -> list[dict]:
    """Windows' search index: every indexed file under your user folder (a thread with a time limit)."""
    found: list[dict] = []

    def run() -> None:
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            conn = win32com.client.Dispatch("ADODB.Connection")
            conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
            like = " AND ".join(f"System.FileName LIKE '%{w.replace(chr(39), '')}%'" for w in words[:4])
            rs, _ = conn.Execute(f"SELECT TOP {limit} System.ItemPathDisplay, System.DateModified FROM SYSTEMINDEX "
                                 f"WHERE {like} AND SCOPE='file:{str(HOME).replace(chr(92), '/')}'")
            while not rs.EOF:
                p = str(rs.Fields.Item(0).Value)
                if not any(s in p.split(os.sep) for s in SKIP):
                    found.append({"path": p, "name": os.path.basename(p), "dir": os.path.isdir(p), "mtime": os.path.getmtime(p) if os.path.exists(p) else 0})
                rs.MoveNext()
        except Exception:  # noqa: BLE001 - no index, no problem: the scan already ran
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(6)
    return list(found)


def find(query: str, folder: str | None = None, kind: str | None = None, want_dir: bool = False) -> list[dict]:
    """Best matches for a spoken file name, best first: [{path, name, dir, mtime, score}]."""
    words = _words(query)
    exts = KINDS.get((kind or "").casefold())
    words = [w for w in words if w not in KINDS]  # "the physics pdf": the kind isn't part of the name
    if not words:
        return []
    root = known_folder(folder) if folder else None
    pool = [e for e in entries() if not root or e["path"].startswith(str(root))]

    def rank(items: list[dict]) -> list[dict]:
        out = []
        for e in items:
            if want_dir and not e["dir"]:
                continue
            if exts and (e["dir"] or Path(e["name"]).suffix.casefold() not in exts):
                continue
            s = _score(words, e["name"], os.path.basename(os.path.dirname(e["path"])))
            if s > 0:
                out.append({**e, "score": s - (0.3 if e["dir"] and not want_dir else 0)})
        return sorted(out, key=lambda e: (e["score"], e["mtime"]), reverse=True)

    best = rank(pool)
    if not best and not root:
        best = rank(_index_search(words))
    return best[:5]


def open_path(path: str) -> str:
    """Open it with its default app; scripts and installers are shown in Explorer instead. -> "opened" | "shown"."""
    p = Path(path)
    if not p.exists():
        raise FileError("That file isn't there any more.", "missing")
    if p.suffix.casefold() in NEVER_RUN:
        reveal(path)
        return "shown"
    os.startfile(str(p))
    return "opened"


def reveal(path: str) -> None:
    p = Path(path)
    subprocess.Popen(["explorer.exe", str(p)] if p.is_dir() else ["explorer.exe", f"/select,{p}"])


def listing(folder: str, limit: int = 8) -> tuple[Path, list[dict]]:
    """The newest things in a folder ("what's on my desktop")."""
    root = known_folder(folder)
    if not root or not root.exists():
        raise FileError(f"I can't find your {folder} folder.", "no_folder")
    items = []
    with os.scandir(root) as it:
        for e in it:
            if e.name.startswith((".", "~$")) or e.name.casefold() == "desktop.ini":
                continue
            try:
                items.append({"name": e.name, "dir": e.is_dir(), "mtime": e.stat().st_mtime, "path": e.path})
            except OSError:
                continue
    items.sort(key=lambda e: e["mtime"], reverse=True)
    return root, items[:limit] if limit else items
