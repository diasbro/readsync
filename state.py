"""Reading state of a book, kept per device so that a library shared through iCloud never has two
writers on one file. Every device writes only `books/<slug>/state/<device>.json`; everyone reads all
of them and merges: the newest value of each key wins by its `<key>At` timestamp, the reading
statistics add up (each file holds only its own device's sessions), and a sentence position saved
against another edition of the text is dropped, because sentence numbers mean nothing across texts.

The merge is also implemented by the iPhone app; `tests/state_vectors.json` is the shared contract.
Files merge in ascending file-name order, and on equal `<key>At` the earlier file keeps its value.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import tomllib
import uuid
from pathlib import Path

LWW = ("pos", "sent", "mode", "opened", "shelf")  # last writer wins, by "<key>At"
DEVICE_FILE = re.compile(r"^[0-9a-f]{32}\.json$")  # «<id> 2.json», a sync conflict copy, is not a device
DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")
LOCK = threading.Lock()
# a device file that cannot be read right now (not downloaded from iCloud yet) keeps its last value
_last_good: dict[Path, dict] = {}


class StateUnavailable(Exception):
    """This device's own file is there but cannot be read now: writing would replace it with less."""


def device_id() -> str:
    """This Mac's id, kept outside the library so a copied library never makes two Macs one device.
    Anything but 32 lowercase hex digits would not be read back as a device file, so it is replaced."""
    env = os.environ.get("READSYNC_DEVICE", "")
    if DEVICE_ID.match(env):
        return env
    f = Path.home() / "Library" / "Application Support" / "readsync" / "device-id"
    try:
        old = f.read_text(encoding="utf-8").strip()
    except OSError:
        old = ""
    if DEVICE_ID.match(old):
        return old
    f.parent.mkdir(parents=True, exist_ok=True)
    new = uuid.uuid4().hex
    tmp = f.with_name("." + f.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, f)
    return new


def _num(v: object) -> float | None:
    """A JSON number, or None for anything else (true and false included)."""
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def edition_of(book_dir: Path) -> str:
    try:
        return str(tomllib.loads((book_dir / "book.toml").read_text(encoding="utf-8")).get("edition", ""))
    except (OSError, ValueError):
        return ""


def _parse(p: Path) -> dict | None:
    """The file's state, or None when it cannot be read. JSON that is not an object counts as empty."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        data = {}
    _last_good[p] = data
    return data


def _read(p: Path) -> dict:
    data = _parse(p)
    return _last_good.get(p, {}) if data is None else data


def _own_path(book_dir: Path) -> Path:
    return book_dir / "state" / f"{device_id()}.json"


def _migrate(book_dir: Path) -> None:
    """The single state file of earlier versions becomes this Mac's. Its sentence was saved against the
    text the book has now, so it is stamped with that edition before another edition can come in."""
    own = _own_path(book_dir)
    legacy = book_dir / "state.json"
    if not legacy.exists() or own.exists():
        return
    own.parent.mkdir(exist_ok=True)
    data = _parse(legacy)
    if data is not None and "sent" in data and not data.get("sentEdition"):
        data["sentEdition"] = edition_of(book_dir)
        tmp = own.with_name("." + own.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, own)
        legacy.unlink()
    else:
        os.replace(legacy, own)


def _own_for_write(book_dir: Path) -> dict:
    """A copy of this device's state to change and save. A file that is there but cannot be read (still
    an iCloud placeholder, or damaged) is never taken for an empty one: saving would wipe it."""
    _migrate(book_dir)
    own = _own_path(book_dir)
    if own.exists():
        data = _parse(own)
        if data is None:
            data = _last_good.get(own)
        if data is None:
            raise StateUnavailable(f"reading state not readable yet: {own.name}")
    elif own.with_name("." + own.name + ".icloud").exists():
        raise StateUnavailable(f"reading state not downloaded from iCloud yet: {own.name}")
    else:
        data = {}
    return copy.deepcopy(data)  # the cached dict changes only once the save has landed


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
    for st in files:  # in file-name order: on equal times the earlier file keeps its value
        for key in LWW:
            if key not in st:
                continue
            if key == "sent" and edition and st.get("sentEdition") and st["sentEdition"] != edition:
                continue
            at = _num(st.get(key + "At")) or 0  # a time that is not a number counts as none
            if key not in out or at > out.get(key + "At", 0):
                out[key], out[key + "At"] = st[key], at
                if key == "sent":
                    out["sentPct"] = st.get("sentPct", 0)
    days: dict = {}
    for st in files:
        stats = st.get("stats")
        file_days = stats.get("days") if isinstance(stats, dict) else None
        for day, v in (file_days if isinstance(file_days, dict) else {}).items():
            if not isinstance(v, dict):
                continue
            cur = days.setdefault(day, {"sec": 0.0, "words": 0.0})
            cur["sec"] += float(_num(v.get("sec")) or 0)
            cur["words"] += float(_num(v.get("words")) or 0)
    if days:
        out["stats"] = {"days": days}
    return out


def load(book_dir: Path) -> dict:
    """What the reader sees: every device's state merged."""
    with LOCK:
        _migrate(book_dir)  # the old single file becomes this Mac's before it is read as a device
        d = book_dir / "state"
        files = [_read(p) for p in sorted(d.iterdir()) if DEVICE_FILE.match(p.name)] if d.is_dir() else []
        return merge(files, edition_of(book_dir))


def put(book_dir: Path, patch: dict) -> dict:
    """A change from this device's reader. Only the LWW keys are taken: statistics come from sessions
    alone, so a reader echoing back the merged totals cannot make them count twice."""
    with LOCK:
        st = _own_for_write(book_dir)
        for key in LWW:
            at = _num(patch.get(key + "At", 0))
            if key in patch and at is not None and at >= (_num(st.get(key + "At")) or 0):
                st[key], st[key + "At"] = patch[key], at
                if key == "sent":
                    st["sentPct"] = patch.get("sentPct", 0)
                    st["sentEdition"] = edition_of(book_dir)
        _save_own(book_dir, st)
    return load(book_dir)


def add_session(book_dir: Path, day: str, sec: float, words: float) -> dict:
    with LOCK:
        st = _own_for_write(book_dir)
        days = st.setdefault("stats", {}).setdefault("days", {})
        cur = days.get(day, {"sec": 0, "words": 0})
        days[day] = {"sec": cur["sec"] + max(0.0, sec), "words": cur["words"] + max(0.0, words)}
        _save_own(book_dir, st)
    return load(book_dir)


def forget_sent(book_dir: Path) -> None:
    """New text, new sentence numbering: this device's page position starts over. Other devices'
    positions fall away by edition once the new text is in, and so does this one when its file cannot
    be read now: it is left as it is rather than written over."""
    with LOCK:
        try:
            st = _own_for_write(book_dir)
        except StateUnavailable:
            return
        for k in ("sent", "sentAt", "sentPct", "sentEdition"):
            st.pop(k, None)
        _save_own(book_dir, st)
