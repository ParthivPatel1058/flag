"""What PLAG makes for you (PDFs and 3D models) stays a draft until you say "save" (the user's choice, 2026-09-25):
it's kept in PLAG's memory and shown on the dashboard, and nothing is written to your Documents or Desktop.

"Save" (said, or the Save button) writes it to Documents\\PLAG\\<Writing | Reports | 3D>. Opening a draft in another
app (your PDF reader, 3D Viewer) needs a file, so that uses a temporary copy in the system temp folder, deleted when
PLAG starts and when it quits. Only the last few drafts are kept (a few MB); an unsaved draft is gone after a restart.
"""

import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

MAX = 8
TEMP = Path(tempfile.gettempdir()) / "PLAG-drafts"


@dataclass
class Draft:
    id: str
    kind: str  # "pdf" or "3d"
    title: str
    data: bytes
    folder: Path  # where "save" puts it
    filename: str
    saved: Path | None = None
    created: float = field(default_factory=time.time)

    def info(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "saved": str(self.saved) if self.saved else "",
                "bytes": len(self.data)}


_drafts: "OrderedDict[str, Draft]" = OrderedDict()
_lock = threading.Lock()


def add(kind: str, title: str, data: bytes, folder: Path, filename: str) -> Draft:
    d = Draft(uuid.uuid4().hex[:10], kind, title, data, folder, filename)
    with _lock:
        _drafts[d.id] = d
        while len(_drafts) > MAX:
            _drafts.popitem(last=False)
    return d


def get(draft_id: str) -> Draft | None:
    with _lock:
        return _drafts.get(draft_id)


def latest(kind: str = "") -> Draft | None:
    """The newest draft (of that kind): the one "save" means."""
    with _lock:
        for d in reversed(_drafts.values()):
            if not kind or d.kind == kind:
                return d
    return None


def save(d: Draft) -> Path:
    """Write it to its folder in Documents (a new name if that one's taken); saving twice keeps the first file."""
    if d.saved and d.saved.exists():
        return d.saved
    d.folder.mkdir(parents=True, exist_ok=True)
    path = d.folder / d.filename
    n = 2
    while path.exists():
        path = d.folder / f"{Path(d.filename).stem} ({n}){Path(d.filename).suffix}"
        n += 1
    path.write_bytes(d.data)
    d.saved = path
    return path


def temp_copy(d: Draft) -> Path:
    """A file to open it with another app: the saved file, or a temporary copy (deleted when PLAG starts or quits)."""
    if d.saved and d.saved.exists():
        return d.saved
    TEMP.mkdir(parents=True, exist_ok=True)
    path = TEMP / f"{d.id}_{d.filename}"
    if not path.exists():
        path.write_bytes(d.data)
    return path


def clear_temp() -> None:
    shutil.rmtree(TEMP, ignore_errors=True)
