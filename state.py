"""Reading state of a book, kept per device so that a library shared through iCloud never has two
writers on one file. Every device writes only `books/<slug>/state/<device>.json`; everyone reads all
of them and merges: the newest value of each key wins by its `<key>At` timestamp, the reading
statistics add up (each file holds only its own device's sessions), and a sentence position saved
against another edition of the text is dropped, because sentence numbers mean nothing across texts.

The merge is also implemented by the iPhone app; `tests/state_vectors.json` is the shared contract.
"""

from __future__ import annotations

import json
import os
import re
import threading
import tomllib
import uuid
from pathlib import Path

LWW = ("pos", "sent", "mode", "opened", "shelf")  # last writer wins, by "<key>At"
DEVICE_FILE = re.compile(r"^[0-9a-f]{32}\.json$")  # «<id> 2.json», a sync conflict copy, is not a device
LOCK = threading.Lock()
# a device file that cannot be read right now (not downloaded from iCloud yet) keeps its last value
_last_good: dict[Path, dict] = {}


def device_id() -> str:
    """This Mac's id, kept outside the library so a copied library never makes two Macs one device."""
    if os.environ.get("READSYNC_DEVICE"):
        return os.environ["READSYNC_DEVICE"]
    f = Path.home() / "Library" / "Application Support" / "readsync" / "device-id"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        f.parent.mkdir(parents=True, exist_ok=True)
        new = uuid.uuid4().hex
        f.write_text(new, encoding="utf-8")
        return new


def edition_of(book_dir: Path) -> str:
    try:
        return str(tomllib.loads((book_dir / "book.toml").read_text(encoding="utf-8")).get("edition", ""))
    except (OSError, ValueError):
        return ""


def _read(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _last_good.get(p, {})
    _last_good[p] = data
    return data


def _own_path(book_dir: Path) -> Path:
    return book_dir / "state" / f"{device_id()}.json"


def _load_own(book_dir: Path) -> dict:
    own = _own_path(book_dir)
    legacy = book_dir / "state.json"
    if legacy.exists() and not own.exists():  # the single state file of earlier versions becomes this Mac's
        own.parent.mkdir(exist_ok=True)
        os.replace(legacy, own)
    return _read(own) if own.exists() else {}


def _save_own(book_dir: Path, st: dict) -> None:
    own = _own_path(book_dir)
    own.parent.mkdir(exist_ok=True)
    tmp = own.with_name("." + own.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, own)
    _last_good[own] = st


def merge(files: list[dict], edition: str) -> dict:
    """The merged view of several devices' state. Pure: the shared test vectors run against it."""
    out: dict = {}
    for st in files:
        for key in LWW:
            if key not in st:
                continue
            if key == "sent" and edition and st.get("sentEdition") and st["sentEdition"] != edition:
                continue
            at = st.get(key + "At", 0) or 0
            if key not in out or at > out.get(key + "At", 0):
                out[key], out[key + "At"] = st[key], at
                if key == "sent":
                    out["sentPct"] = st.get("sentPct", 0)
    days: dict = {}
    for st in files:
        for day, v in (st.get("stats") or {}).get("days", {}).items():
            cur = days.setdefault(day, {"sec": 0.0, "words": 0.0})
            cur["sec"] += float(v.get("sec", 0) or 0)
            cur["words"] += float(v.get("words", 0) or 0)
    if days:
        out["stats"] = {"days": days}
    return out


def load(book_dir: Path) -> dict:
    """What the reader sees: every device's state merged."""
    with LOCK:
        _load_own(book_dir)  # migrates the old single file before it is read as a device
        d = book_dir / "state"
        files = [_read(p) for p in sorted(d.iterdir()) if DEVICE_FILE.match(p.name)] if d.is_dir() else []
        return merge(files, edition_of(book_dir))


def put(book_dir: Path, patch: dict) -> dict:
    """A change from this device's reader. Only the LWW keys are taken: statistics come from sessions
    alone, so a reader echoing back the merged totals cannot make them count twice."""
    with LOCK:
        st = _load_own(book_dir)
        for key in LWW:
            if key in patch and (patch.get(key + "At", 0) or 0) >= (st.get(key + "At", 0) or 0):
                st[key], st[key + "At"] = patch[key], patch.get(key + "At", 0)
                if key == "sent":
                    st["sentPct"] = patch.get("sentPct", 0)
                    st["sentEdition"] = edition_of(book_dir)
        _save_own(book_dir, st)
    return load(book_dir)


def add_session(book_dir: Path, day: str, sec: float, words: float) -> dict:
    with LOCK:
        st = _load_own(book_dir)
        days = st.setdefault("stats", {}).setdefault("days", {})
        cur = days.get(day, {"sec": 0, "words": 0})
        days[day] = {"sec": cur["sec"] + max(0.0, sec), "words": cur["words"] + max(0.0, words)}
        _save_own(book_dir, st)
    return load(book_dir)


def forget_sent(book_dir: Path) -> None:
    """New text, new sentence numbering: this device's page position starts over. Other devices'
    positions fall away by edition once the new text is in."""
    with LOCK:
        st = _load_own(book_dir)
        for k in ("sent", "sentAt", "sentPct", "sentEdition"):
            st.pop(k, None)
        _save_own(book_dir, st)
