"""archive.org itself: the advanced search and an item's metadata, shared by `sources/audio/archive.py` and
`sources/internet_archive.py`. One host, so one budget of requests in flight for both (`SLOTS`)."""

from __future__ import annotations

import json
import re
import threading

from . import base

BASE = "https://archive.org"
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", re.ASCII)  # an identifier that is safe to put in a URL
SLOTS = threading.BoundedSemaphore(2)  # archive.org answers many requests badly: two at a time


def fetch(url: str) -> dict:
    with SLOTS:
        return json.loads(base.get(url, timeout=20).decode("utf-8", "replace"))


def parse_search(data: object) -> list[dict]:
    """The docs of a search answer whose identifier can be put in a URL."""
    response = data.get("response") if isinstance(data, dict) else None
    docs = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(docs, list):
        return []
    return [d for d in docs if isinstance(d, dict) and ID_RE.fullmatch(str(d.get("identifier") or ""))]


def joined(v: object) -> str:
    """A metadata value that may be one string or a list of them, as one line."""
    return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v or "")
