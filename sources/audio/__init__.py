"""Audio sources: recordings of a book to listen to before loading, and their parts for the pipeline.
Searched in this order: knigavuhe.org (the main catalog), YouTube (the one with captions), archive.org.

A recording is named by its `ref`, the only thing a client ever sends back:
`knigavuhe:<bookId>:<slug>`, `yt:<videoId>[,<videoId>…]` or `ia:<identifier>`. Every upstream URL is
derived from a ref on the server, never taken from a client: that is the guard against the proxy
being pointed elsewhere. `parts(ref)` and `stream_url(ref, part)` serve the pipeline and the proxy."""

from __future__ import annotations

import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait

from . import archive, knigavuhe, youtube

SOURCES = [(knigavuhe.NAME, knigavuhe.search), (youtube.NAME, youtube.search), (archive.NAME, archive.search)]
ROUND_SECONDS = 30  # one deadline for the whole search
CACHE_SECONDS = 900
PARTS_SECONDS = 600  # track links of knigavuhe live ~77 h: ten minutes is well inside

REFS = {
    "knigavuhe": re.compile(r"knigavuhe:([0-9]{1,9}):([a-z0-9][a-z0-9_-]{0,199})", re.ASCII),
    "yt": re.compile(r"yt:([A-Za-z0-9_-]{11}(?:,[A-Za-z0-9_-]{11}){0,49})", re.ASCII),
    "ia": re.compile(r"ia:([A-Za-z0-9][A-Za-z0-9._-]{0,99})", re.ASCII),
}

CACHE: dict[str, tuple[float, dict]] = {}
PARTS: dict[str, tuple[float, list[dict]]] = {}


class BadRef(ValueError):
    """A ref that names no recording this server knows how to find: the client's mistake (400)."""


def parse_ref(ref: str) -> tuple[str, tuple]:
    """('knigavuhe', (bookId, slug)) | ('yt', ([ids],)) | ('ia', (identifier,)), or BadRef."""
    if isinstance(ref, str) and len(ref) <= 700:
        for kind, rx in REFS.items():
            m = rx.fullmatch(ref)
            if m:
                if kind == "knigavuhe":
                    return kind, (int(m.group(1)), m.group(2))
                if kind == "yt":
                    return kind, (m.group(1).split(","),)
                return kind, (m.group(1),)
    raise BadRef("неизвестная озвучка")


def search(query: str) -> dict:
    """Every source at once, within one deadline: {hits, errors}. LitRes trials are not offered."""
    key = " ".join(query.lower().replace("ё", "е").split())
    cached = CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    hits: list[dict] = []
    errors: list[str] = []
    ex = ThreadPoolExecutor(max_workers=len(SOURCES))
    futures = {ex.submit(fn, query): name for name, fn in SOURCES}
    done, _ = wait(futures, timeout=ROUND_SECONDS)
    ex.shutdown(wait=False, cancel_futures=True)  # a late source is not waited for
    for f, name in futures.items():  # in source order, so the same search returns the same list
        if f not in done:
            errors.append(f"{name}: не ответил за {ROUND_SECONDS} с")
            continue
        try:
            hits += [h for h in f.result() if not h.get("licensed_trial")]
        except Exception as e:  # noqa: BLE001 - a site down or changed: the others still answer
            errors.append(f"{name}: {e}")
    out = {"hits": hits, "errors": errors}
    if not errors:
        CACHE[key] = (time.time(), out)
    return out


def parts(ref: str) -> list[dict]:
    """The parts of a recording as they are now: [{title, duration, url, size}], links fresh."""
    kind, args = parse_ref(ref)
    cached = PARTS.get(ref)
    if cached and time.time() - cached[0] < PARTS_SECONDS:
        return cached[1]
    if kind == "knigavuhe":
        ps = knigavuhe.parts(*args)
    elif kind == "yt":
        ps = youtube.parts(*args)
    else:
        ps = archive.parts(*args)
    PARTS[ref] = (time.time(), ps)
    return ps


def stream_url(ref: str, part: int) -> tuple[str, dict]:
    """(URL, extra request headers) of one part, to stream it. BadRef for a part the recording lacks."""
    kind, _ = parse_ref(ref)
    ps = parts(ref)
    if not 0 <= part < len(ps):
        raise BadRef("нет такой части")
    url = youtube.stream(ps[part]["id"]) if kind == "yt" else ps[part]["url"]
    if urllib.parse.urlsplit(url).scheme != "https":
        raise RuntimeError("источник дал ссылку не по https")
    return url, {}
