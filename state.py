"""Reading state of a book, kept per device so that a library shared through iCloud never has two
writers on one file. Every device writes only `books/<slug>/state/<device>.json`; everyone reads all
of them and merges: the newest value of each key wins by its `<key>At` timestamp, the reading
statistics add up (each file holds only its own device's sessions), and a sentence position saved
against another edition of the text is dropped, because sentence numbers mean nothing across texts,
unless the book's `editions.json` maps that edition: a text extracted again (pipeline/reextract.py)
leaves `{"edition": "<the edition the maps lead to>", "maps": {"<old edition>": [new sentence index for
each old one], ...}}`, and a sentence from a mapped edition is translated (clamped to the map) and counts
as the current edition's. The maps are used only while the book's edition is the one they lead to: a copy
of an older text (as the phone may still hold one), or a newer text whose stale map has not been deleted
yet, does not use them. The merged state names the edition its `sent` counts in (`sentEdition`), so a page
still showing another text can tell.

The merge is also implemented by the iPhone app; `tests/state_vectors.json` is the shared contract.
Files merge in ascending file-name order, and on equal `<key>At` the earlier file keeps its value.

`shelf` is the book's status: "" (none: derived from reading), "reading", "paused" or "done"; the old
"library" reads as "paused". `finished` is the list of local days the book was finished on, written
whole: a writer takes the merged list, adds or drops a day and writes it all, so an undo on one device
wins over an older list on another. `status()` derives what the library shows; `tests/status_vectors.json`
is its contract with the iPhone app.
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

LWW = ("pos", "sent", "mode", "opened", "shelf", "finished")  # last writer wins, by "<key>At"
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


def editions_of(book_dir: Path) -> dict:
    """The book's editions.json: `{"edition": <the edition its maps lead to>, "maps": {<older edition>: [the new
    sentence index of each of its sentences]}}`. Missing, unreadable or not naming its edition: no map ({}).
    Entries that are not a list of whole numbers are left out."""
    try:
        data = json.loads((book_dir / "editions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("edition"), str) or not isinstance(data.get("maps"), dict):
        return {}
    maps = {
        k: v
        for k, v in data["maps"].items()
        if isinstance(v, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in v)
    }
    return {"edition": data["edition"], "maps": maps}


def maps_for(editions: dict | None, edition: str) -> dict[str, list[int]]:
    """The maps of an editions.json that count in `edition`: only those leading to it."""
    if not editions or not edition or editions.get("edition") != edition or not isinstance(editions.get("maps"), dict):
        return {}
    return editions["maps"]


DROPPED = object()  # a sentence that does not count in the edition asked for


def _sent(st: dict, edition: str, maps: dict[str, list[int]]) -> object:
    """The sentence of `st` as it counts in `edition`: as saved when it names no other edition, translated
    when `maps` (those of editions.json leading to `edition`, see maps_for) map the one it names, else DROPPED."""
    ed = st.get("sentEdition")
    if not edition or not ed or ed == edition:
        return st["sent"]
    m = maps.get(ed) if isinstance(ed, str) else None
    n = _num(st["sent"])
    if not m or n is None or n != n:  # NaN: no place at all
        return DROPPED
    return m[int(min(max(n, 0), len(m) - 1))]


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


def merge(files: list[dict], edition: str, editions: dict | None = None) -> dict:
    """The merged view of several devices' state. Pure: the shared test vectors run against it.
    `editions`: the book's editions.json, which carries sentences of older editions over when it leads to
    `edition`. A merged `sent` comes with `sentEdition`: `edition`, the text it counts in."""
    maps = maps_for(editions, edition)
    out: dict = {}
    for st in files:  # in file-name order: on equal times the earlier file keeps its value
        for key in LWW:
            if key not in st:
                continue
            value = _sent(st, edition, maps) if key == "sent" else st[key]
            if value is DROPPED:
                continue
            at = _num(st.get(key + "At")) or 0  # a time that is not a number counts as none
            if key not in out or at > out.get(key + "At", 0):
                out[key], out[key + "At"] = value, at
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
    if "sent" in out:
        out["sentEdition"] = edition
    return out


def status(merged: dict, audio: bool, at_end: bool) -> dict:
    """The book's status from its merged state. `audio`: the book has audio, so unless it was left in page
    mode its time position is the one that counts; `at_end`: the position is at the end by the rule used
    before statuses existed (the last spread, or within a minute of the end), which is how a book finished
    then reads as done. A paused book read since it was paused is reading again; a done one stays done
    however its position moves (looking up a quote is not rereading). Pure: shared vectors run against it."""
    shelf = merged.get("shelf")
    finished = merged.get("finished")
    days = [d for d in finished if isinstance(d, str)] if isinstance(finished, list) else []
    stats = merged.get("stats")
    read_days = stats.get("days") if isinstance(stats, dict) else None
    read_days = read_days if isinstance(read_days, dict) else {}
    if shelf == "done":
        st = "done"
    elif shelf == "reading":
        st = "reading"
    elif shelf in ("paused", "library"):
        key = "posAt" if audio and merged.get("mode") != "pages" else "sentAt"
        st = "reading" if (_num(merged.get(key)) or 0) > (_num(merged.get("shelfAt")) or 0) else "paused"
    elif at_end:
        st = "done"
    else:
        seconds = sum(_num(v.get("sec")) or 0 for v in read_days.values() if isinstance(v, dict))
        st = "reading" if seconds > 600 else "none"
    on = max(days) if days else (max(read_days) if st == "done" and read_days else None)
    return {"status": st, "rereading": st == "reading" and bool(days), "finishedOn": on}


def load(book_dir: Path) -> dict:
    """What the reader sees: every device's state merged."""
    with LOCK:
        _migrate(book_dir)  # the old single file becomes this Mac's before it is read as a device
        d = book_dir / "state"
        files = [_read(p) for p in sorted(d.iterdir()) if DEVICE_FILE.match(p.name)] if d.is_dir() else []
        return merge(files, edition_of(book_dir), editions_of(book_dir))


def put(book_dir: Path, patch: dict) -> dict:
    """A change from this device's reader. Only the LWW keys are taken: statistics come from sessions
    alone, so a reader echoing back the merged totals cannot make them count twice. A sentence comes with
    the edition the page loaded (`sentEdition`): one counted in another text than the book's now is not taken,
    unless editions.json maps that text: then it is translated and saved as the current edition's."""
    with LOCK:
        st = _own_for_write(book_dir)
        edition = edition_of(book_dir) if "sent" in patch else ""
        other = edition and patch.get("sentEdition") not in (None, "", edition)
        maps = maps_for(editions_of(book_dir), edition) if other else {}
        for key in LWW:
            at = _num(patch.get(key + "At", 0))
            if key == "finished" and not isinstance(patch.get(key), list):
                continue  # a list of days or nothing: anything else would be merged as one
            value = _sent(patch, edition, maps) if key == "sent" and key in patch else patch.get(key)
            if value is DROPPED:
                continue  # a page still showing the text this book had before, and no map for it
            if key in patch and at is not None and at >= (_num(st.get(key + "At")) or 0):
                st[key], st[key + "At"] = value, at
                if key == "sent":
                    st["sentPct"] = patch.get("sentPct", 0)
                    st["sentEdition"] = edition
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
