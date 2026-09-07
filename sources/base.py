"""Shared pieces for text sources: fetching, title normalisation, volume numbers, and the grouping
of raw hits into editions. A source module returns plain dicts (see `sources/__init__.py`)."""

from __future__ import annotations

import html
import re
import urllib.request

VOLUME_RE = re.compile(r"\b(?:т|том|кн|книга|ч|часть|vol|volume|part)\.?\s*(\d+|[IVXLC]+)\b", re.I)
SOURCE_ORDER = ("fantasy-worlds", "flibusta", "coollib")  # priority when rows are sorted


def get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def unescape(s: str) -> str:
    return html.unescape(s)


def norm_title(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = VOLUME_RE.sub(" ", s)
    s = re.sub(r"\bгл\.?\s.*$", " ", s)  # chapter ranges after the volume ("Гл. I - XL")
    s = re.sub(r"\[[^\]]*\]|\([^)]*$", " ", s)  # edition tags like [litres], [СИ]; dangling "("
    return re.sub(r"[^\w]+", " ", s).strip()


def roman_to_int(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = vals.get(ch, 0)
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def volume_no(title: str) -> int | None:
    m = VOLUME_RE.search(title or "")
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else roman_to_int(v)


def hit(source: str, title: str, url: str, kind: str, **extra) -> dict:
    """A raw hit as a source reports it. Only facts the catalog states; nothing is judged here."""
    h = {
        "source": source,
        "title": title,
        "author": "",
        "translator": "",
        "year": "",
        "size_kb": None,
        "lang": "",
        "url": url,
        "kind": kind,
        "audio_url": "",
        "narrator": "",
        "readable": True,
    }
    h.update(extra)
    return h


def editions(hits: list[dict]) -> list[dict]:
    """One row per edition. Numbered volumes of the same edition (same source, work, author, translator)
    load together as one book; anything else stays a row of its own."""
    groups: dict[tuple, list[dict]] = {}
    for h in hits:
        key = (h["source"], norm_title(h["title"]), norm_title(h["author"]), norm_title(h.get("translator", "")))
        groups.setdefault(key, []).append(h)
    rows = []
    for parts in groups.values():
        volumes = sorted((p for p in parts if volume_no(p["title"])), key=lambda p: volume_no(p["title"]))
        for p in parts:
            if not volume_no(p["title"]):
                rows.append(dict(p, parts=[{"title": p["title"], "url": p["url"], "kind": p["kind"]}]))
        if volumes:
            seen: set = set()
            vols = [p for p in volumes if not (volume_no(p["title"]) in seen or seen.add(volume_no(p["title"])))]
            first = vols[0]
            base = re.sub(r"[\s.,:;(–—-]+$", "", VOLUME_RE.split(first["title"])[0]).strip() or first["title"]
            labels = [VOLUME_RE.search(p["title"]).group(0) for p in vols]
            rows.append(
                dict(
                    first,
                    title=base,
                    parts=[{"title": p["title"], "url": p["url"], "kind": p["kind"]} for p in vols],
                    parts_label=", ".join(labels),
                )
            )
    order = {n: i for i, n in enumerate(SOURCE_ORDER)}
    rows.sort(key=lambda r: (order.get(r["source"], 9), -int(r["year"] or 0), r["title"]))
    for r in rows:
        r.pop("url", None)
    return rows
