"""archive.org, LibriVox included: `advancedsearch.php` finds items, `/metadata/<id>` lists their files
with length and size, so duration and size are exact before anything is downloaded. The language
metadata is too sparse to filter on: every word of the query must be in the title or the creator."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .. import base

NAME = "archive.org"
BASE = "https://archive.org"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", re.ASCII)
ROWS = 8
SLOTS = threading.BoundedSemaphore(2)
FORMATS = ("VBR MP3", "128Kbps MP3", "64Kbps MP3", "MP3")  # one set of files per item, best first


def fetch(url: str) -> dict:
    with SLOTS:
        return json.loads(base.get(url, timeout=20).decode("utf-8", "replace"))


def lucene(query: str) -> str:
    words = base.terms(query)[:6]  # only \w characters: nothing to escape
    if not words:
        return ""
    return " AND ".join(["mediatype:audio", *(f"(title:({w}) OR creator:({w}))" for w in words)])


def search(query: str) -> list[dict]:
    q = lucene(query)
    if not q:
        return []
    fields = "".join(f"&fl[]={f}" for f in ("identifier", "title", "creator", "collection", "item_size"))
    docs = parse_search(fetch(f"{BASE}/advancedsearch.php?q={urllib.parse.quote(q)}{fields}&rows={ROWS}&output=json"))
    with ThreadPoolExecutor(max_workers=2) as ex:  # the two request slots: metadata of each item
        metas = list(ex.map(lambda d: safe_metadata(d["identifier"]), docs))
    return [h for h in (to_hit(d, m) for d, m in zip(docs, metas, strict=True)) if h]


def parse_search(data: dict) -> list[dict]:
    return [
        d for d in (data.get("response") or {}).get("docs") or [] if ID_RE.fullmatch(str(d.get("identifier") or ""))
    ]


def safe_metadata(identifier: str) -> dict | None:
    try:
        return fetch(f"{BASE}/metadata/{identifier}")
    except Exception:  # noqa: BLE001 - one item that does not answer leaves the others in the list
        return None


def joined(v) -> str:
    return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v or "")


def seconds(length) -> float | None:
    """`length` is seconds ("1234.56") or a clock ("20:34", "1:02:03")."""
    s = str(length or "").strip()
    try:
        if ":" in s:
            total = 0.0
            for x in s.split(":"):
                total = total * 60 + float(x)
            return total
        return float(s) if s else None
    except ValueError:
        return None


def natural(name: str) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name)]


def parse_metadata(identifier: str, data: dict) -> list[dict]:
    """The item's mp3 files in order, from one format (the best the item has): [{title, duration, url, size}]."""
    files = [f for f in data.get("files") or [] if str(f.get("name") or "").lower().endswith(".mp3")]
    by_format: dict[str, list[dict]] = {}
    for f in files:
        by_format.setdefault(str(f.get("format") or ""), []).append(f)
    if not by_format:
        return []
    fmt = next((x for x in FORMATS if x in by_format), max(by_format, key=lambda k: len(by_format[k])))
    chosen = sorted(by_format[fmt], key=lambda f: natural(str(f["name"])))
    out = []
    for f in chosen:
        name = str(f["name"])
        size = str(f.get("size") or "")
        out.append(
            {
                "title": str(f.get("title") or name.rsplit("/", 1)[-1].rsplit(".", 1)[0]),
                "duration": seconds(f.get("length")),
                "url": f"{BASE}/download/{identifier}/{urllib.parse.quote(name)}",
                "size": int(size) if size.isdigit() else None,
            }
        )
    return out


def to_hit(doc: dict, meta: dict | None) -> dict | None:
    identifier = doc["identifier"]
    ps = parse_metadata(identifier, meta or {})
    if not ps:
        return None  # no metadata, or nothing to listen to
    info = (meta or {}).get("metadata") or {}
    duration = sum(p["duration"] or 0 for p in ps) or None
    size = sum(p["size"] or 0 for p in ps) or None
    return base.audio_hit(
        NAME,
        f"ia:{identifier}",
        joined(info.get("title") or doc.get("title")),
        author=joined(info.get("creator") or doc.get("creator")),
        duration_s=int(duration) if duration else None,
        parts=len(ps),
        size_bytes=size,
        bitrate_kbps=round(size * 8 / duration / 1000) if size and duration else None,
        page_url=f"{BASE}/details/{identifier}",
        librivox="librivoxaudio" in joined(info.get("collection") or doc.get("collection")),
    )


def parts(identifier: str) -> list[dict]:
    ps = parse_metadata(identifier, fetch(f"{BASE}/metadata/{identifier}"))
    if not ps:
        raise RuntimeError("в элементе archive.org нет mp3")
    return ps
