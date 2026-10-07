"""knigavuhe.org: an HTML search; a book page carries its tracks as the JSON of `new BookPlayer(...)`.
Track links expire (`url_refresh_in`), so they are read from the page each time they are needed.
Books licensed to LitRes have a single trial track instead: those are flagged and never offered."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse

from bs4 import BeautifulSoup

from .. import base

NAME = "knigavuhe"
BASE = "https://knigavuhe.org"
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,199}", re.ASCII)
BITRATE_KBPS = 128  # every book there is mp3 128 kbit/s CBR: size follows from duration
SLOTS = threading.BoundedSemaphore(2)  # at most two requests to the site at a time


class TrialOnly(RuntimeError):
    pass


def fetch(url: str) -> str:
    with SLOTS:
        return base.get(url, timeout=20).decode("utf-8", "replace")


def search(query: str) -> list[dict]:
    return parse_search(fetch(f"{BASE}/search/?q={urllib.parse.quote(query)}"))


UNITS = {"ч": 3600, "м": 60, "с": 1}


def duration_of(text: str) -> int | None:
    """«97 часов 37 минут» → 351420. Hours, minutes and seconds, in any of their Russian forms."""
    total, seen = 0, False
    for n, unit in re.findall(r"(\d+)\s*(час|ч|мин|м|сек|с)", (text or "").lower()):
        total += int(n) * UNITS[unit[0]]
        seen = True
    return total if seen else None


def text(tag) -> str:
    """A tag's words as shown: a search highlights the query in spans that may split a word."""
    return " ".join(tag.get_text("").split())


def parse_search(html: str) -> list[dict]:
    """Rows of a search page: title, authors, narrators, duration, and whether LitRes holds the book."""
    soup = BeautifulSoup(html, "lxml")
    hits = []
    for item in soup.select("div.bookkitem"):
        name = item.select_one("a.bookkitem_name")
        m = re.fullmatch(r"/book/([^/]+)/?", (name.get("href") or "") if name else "")
        if not m or not SLUG_RE.fullmatch(m.group(1)):
            continue
        slug = m.group(1)
        raw = str(item)  # the id shows in the cover path; 0 when it does not (the page is then trusted by slug)
        found = re.search(r"/covers/([0-9]{1,9})/", raw) or re.search(r'data-(?:book-)?id="([0-9]{1,9})"', raw)
        book_id = found.group(1) if found else "0"
        authors = [text(a) for a in item.select(".bookkitem_author a")]
        readers = [text(a) for a in item.select('a[href^="/reader/"]')]
        time_tag = item.select_one(".bookkitem_meta_time")  # «97 часов 37 минут»
        duration = duration_of(text(time_tag) if time_tag else "")
        trial = item.find(class_=lambda c: bool(c) and "litres" in c) is not None  # `bookkitem_meta_block -litres`
        hits.append(
            base.audio_hit(
                NAME,
                f"knigavuhe:{book_id}:{slug}",
                text(name),
                author=", ".join(a for a in authors if a),
                narrator=", ".join(r for r in readers if r),
                duration_s=duration,
                size_bytes=duration * BITRATE_KBPS * 1000 // 8 if duration else None,
                bitrate_kbps=BITRATE_KBPS,
                page_url=f"{BASE}/book/{slug}/",
                licensed_trial=trial,
            )
        )
    return hits


def parts(book_id: int, slug: str) -> list[dict]:
    return parse_player(fetch(f"{BASE}/book/{slug}/"), book_id)


def own_host(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "knigavuhe.org" or host.endswith(".knigavuhe.org"))


def parse_player(html: str, book_id: int = 0) -> list[dict]:
    """The tracks of a book page in order: [{title, duration, url, size}]. Only links to the site's own
    hosts are kept; a page that offers nothing but a LitRes trial raises TrialOnly."""
    m = re.search(r"new\s+BookPlayer\(\s*([0-9]+)\s*,\s*", html)
    if not m:
        raise RuntimeError("на странице книги нет плеера")
    if book_id and int(m.group(1)) != book_id:
        raise RuntimeError("на странице другая книга")
    if re.search(r'"blocked"\s*:\s*true', html):
        raise RuntimeError("книга недоступна на knigavuhe")
    tracks, _ = json.JSONDecoder().raw_decode(html, m.end())
    out, trial = [], False
    for t in tracks if isinstance(tracks, list) else []:
        url = str(t.get("url") or "")
        if "litres.ru" in url:
            trial = True
            continue
        if not own_host(url):
            continue
        dur = t.get("duration_float") or t.get("duration")
        out.append(
            {"title": str(t.get("title") or ""), "duration": float(dur) if dur else None, "url": url, "size": None}
        )
    if not out and trial:
        raise TrialOnly("только фрагмент LitRes")
    if not out:
        raise RuntimeError("у книги нет частей")
    return out
