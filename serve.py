#!/usr/bin/env python3
"""Local server for readsync: serves the reader UI, book data with HTTP Range support,
and /api/books (list of books/*/book.toml)."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import HTTP
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READER = ROOT / "reader"
BOOKS = ROOT / "books"
STATE_LOCK = threading.Lock()
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JOBS: dict[str, dict] = {}
PIPELINE_PY = ROOT / ".venv" / "bin" / "python"
TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "h",
            "c",
            "ch",
            "sh",
            "sch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ],
        strict=True,
    )
)
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/json", ".json")


def list_books() -> list[dict]:
    out = []
    for toml in sorted(BOOKS.glob("*/book.toml")):
        try:
            meta = tomllib.loads(toml.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            meta = {"slug": toml.parent.name, "title": toml.parent.name, "error": str(e)}
        d = toml.parent
        meta["slug"] = d.name
        meta["ready"] = (d / "book.json").exists()
        meta["audio"] = next((f.name for f in (d / "audio.m4a", d / "audio.mp3", d / "yt.webm") if f.exists()), None)
        meta["has_audio"] = bool(meta["audio"]) and (d / "timing.json").exists()
        job = JOBS.get(d.name)
        meta["building"] = bool(job and job["proc"].poll() is None)
        meta["added"] = int(toml.stat().st_mtime * 1000)
        meta["has_hits"] = (d / "hits.json").exists()
        cover = (
            next((f for f in (d / "images").glob("cover.*") if f.is_file()), None) if (d / "images").is_dir() else None
        )
        meta["cover"] = f"images/{cover.name}" if cover else None
        st = load_state(d.name)
        duration = 0.0
        if (d / "timing.json").exists():
            with contextlib.suppress(OSError, ValueError), (d / "timing.json").open("rb") as fh:
                m = re.search(rb'"duration":\s*([\d.]+)', fh.read(300))
                duration = float(m.group(1)) if m else 0.0
        pos = float(st.get("pos", 0) or 0)
        # finished: the audio position is within a minute of the end, or the last spread of a text-only book
        finished = (duration > 0 and pos >= duration - 60) or (duration == 0 and (st.get("sentPct") or 0) >= 99)
        meta["state"] = {
            "opened": st.get("opened", 0),
            "shelf": st.get("shelf", ""),
            "pos": pos,
            "duration": duration,
            "sent": st.get("sent", 0),
            "sentPct": st.get("sentPct", 0),
            "seconds": sum(v.get("sec", 0) for v in (st.get("stats") or {}).get("days", {}).values()),
            "finished": bool(finished),
        }
        if (d / "timing.json").exists():
            try:
                with (d / "timing.json").open("rb") as fh:
                    head = fh.read(200).decode("utf-8", "ignore")
                m = re.search(r'"source":\s*"(\w+)"', head)
                meta["timing_source"] = m.group(1) if m else "?"
            except OSError:
                pass
        out.append(meta)
    return out


def load_state(slug: str) -> dict:
    p = BOOKS / slug / "state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(slug: str, state: dict) -> None:
    p = BOOKS / slug / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def merge_state(slug: str, patch: dict) -> dict:
    """Last writer wins per key (by client timestamp in <key>At); stats days are merged by max."""
    with STATE_LOCK:
        st = load_state(slug)
        for key in ("pos", "sent", "mode", "settings", "opened", "shelf"):
            if key in patch and patch.get(key + "At", 0) >= st.get(key + "At", 0):
                st[key], st[key + "At"] = patch[key], patch.get(key + "At", 0)
                if key == "sent" and "sentPct" in patch:
                    st["sentPct"] = patch["sentPct"]
        if isinstance(patch.get("stats"), dict):
            days = st.setdefault("stats", {}).setdefault("days", {})
            for day, v in patch["stats"].get("days", {}).items():
                cur = days.get(day, {"sec": 0, "words": 0})
                days[day] = {"sec": max(cur["sec"], v.get("sec", 0)), "words": max(cur["words"], v.get("words", 0))}
        save_state(slug, st)
        return st


def add_session(slug: str, delta: dict) -> dict:
    with STATE_LOCK:
        st = load_state(slug)
        days = st.setdefault("stats", {}).setdefault("days", {})
        day = str(delta.get("day", ""))[:10]
        try:
            sec, words = float(delta.get("sec", 0)), float(delta.get("words", 0))
        except (TypeError, ValueError):
            sec, words = 0.0, 0.0
        cur = days.get(day, {"sec": 0, "words": 0})
        days[day] = {"sec": cur["sec"] + max(0.0, sec), "words": cur["words"] + max(0.0, words)}
        save_state(slug, st)
        return st


SETTINGS_FILE = BOOKS / "settings.json"
WISHLIST_FILE = BOOKS / "wishlist.json"


def load_wishlist() -> list[dict]:
    try:
        return json.loads(WISHLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_wishlist(items: list[dict]) -> None:
    tmp = WISHLIST_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, WISHLIST_FILE)


WISH_FIELDS = ("title", "author", "note", "text_url", "audio_url", "searched")
WISH_JSON = ("hits", "author_hits")  # the last search result stays with the title until it is loaded


def wishlist_add(data: dict) -> list[dict]:
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("нужно название")
    with STATE_LOCK:
        items = load_wishlist()
        if any(i["title"].casefold() == title.casefold() for i in items):
            return items
        item = {"id": f"w{int(time.time() * 1000)}", "added": time.strftime("%Y-%m-%d"), "title": title}
        for k in WISH_FIELDS[1:]:
            item[k] = str(data.get(k, "") or "").strip()
        items.insert(0, item)
        save_wishlist(items)
        return items


def wishlist_update(wid: str, data: dict) -> list[dict]:
    with STATE_LOCK:
        items = load_wishlist()
        for it in items:
            if it["id"] == wid:
                for k in WISH_FIELDS:
                    if k in data:
                        it[k] = str(data[k] or "").strip()
                for k in WISH_JSON:
                    if k in data:
                        it[k] = data[k]
        save_wishlist(items)
        return items


def wishlist_delete(wid: str) -> list[dict]:
    with STATE_LOCK:
        items = [i for i in load_wishlist() if i["id"] != wid]
        save_wishlist(items)
        return items


def save_hits(slug: str, data: dict) -> None:
    """The search result a book was picked from stays next to it, so another edition is one click away."""
    if not SLUG_RE.match(slug) or not (BOOKS / slug).is_dir():
        raise ValueError("unknown book")
    (BOOKS / slug / "hits.json").write_text(
        json.dumps({"hits": data.get("hits") or [], "author_hits": data.get("author_hits")}, ensure_ascii=False),
        encoding="utf-8",
    )


def delete_book(slug: str) -> None:
    """Remove a book directory: text, audio, timing and reading state. The page asks for confirmation first."""
    d = BOOKS / slug
    if not SLUG_RE.match(slug) or not d.is_dir():
        raise ValueError("книга не существует")
    job = JOBS.get(slug)
    if job and job["proc"].poll() is None:
        raise ValueError("книга ещё загружается")
    with STATE_LOCK:
        shutil.rmtree(d)
        JOBS.pop(slug, None)


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def merge_settings(patch: dict) -> dict:
    """Reader settings are global (not per book); last writer wins by client timestamp."""
    with STATE_LOCK:
        cur = load_settings()
        if isinstance(patch.get("settings"), dict) and patch.get("settingsAt", 0) >= cur.get("settingsAt", 0):
            cur = {"settings": patch["settings"], "settingsAt": patch.get("settingsAt", 0)}
            tmp = SETTINGS_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, SETTINGS_FILE)
        return cur


FW_SEARCH = "https://fantasy-worlds.org/search.json?q="
FW_READER = "https://reader.fantasy-worlds.org/book/{id}/read.html"
OPDS_SOURCES = [  # searched after fantasy-worlds, in this order
    ("flibusta", "https://flibusta.is", "https://flibusta.is/opds/search?searchType=books&searchTerm="),
    ("coollib", "https://coollib.net", "https://coollib.net/opds/search?searchType=books&searchTerm="),
]
VOLUME_RE = re.compile(r"\b(?:т|том|кн|книга|ч|часть|vol|volume|part)\.?\s*(\d+|[IVXLC]+)\b", re.I)


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


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


def search_fw(query: str) -> list[dict]:
    data = json.loads(_get(FW_SEARCH + urllib.parse.quote(query)).decode("utf-8", "replace"))
    hits = []
    for b in (data.get("books") or [])[:10]:
        author = " ".join(x for x in (b.get("author_name"), b.get("author_surname")) if x)
        yt = b.get("yt_id")
        hits.append(
            {
                "source": "fantasy-worlds",
                "id": str(b.get("id")),
                "title": b.get("title") or "",
                "author": author,
                "translator": "",
                "year": str(b.get("year") or ""),
                "size_kb": None,
                "lang": b.get("lang_code") or "",
                "url": FW_READER.format(id=b.get("id")),
                "kind": "html",
                "audio_url": f"https://www.youtube.com/watch?v={yt}" if yt else "",
                "narrator": b.get("yt_reader") or "",
            }
        )

    def readable(h):
        try:
            r = urllib.request.Request(h["url"], method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            h["readable"] = urllib.request.urlopen(r, timeout=8).status == 200
        except Exception:  # noqa: BLE001
            h["readable"] = False
        return h

    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(readable, hits))


def opds_entries(name: str, base: str, xml: str) -> list[dict]:
    """Book entries of an OPDS feed as hits. Facts only, as the catalog states them: title, authors,
    translator, year, size, format."""
    hits = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        title = html_unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip() if "<title>" in e else ""
        authors = [html_unescape(x).strip() for x in re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)]
        lang = re.search(r"<dc:language>(.*?)</dc:language>", e)
        fb2 = re.search(r'href="([^"]+)"[^>]*type="application/fb2\+zip"', e) or re.search(
            r'type="application/fb2\+zip"[^>]*href="([^"]+)"', e
        )
        epub = re.search(r'href="([^"]+)"[^>]*type="application/epub\+zip"', e)
        link = fb2 or epub
        if link:
            href = link.group(1)
        else:  # coollib links the book page (or only its cover) from lists: derive /b/<id>/fb2
            page = re.search(r'href="(?:https?://[^/"]+)?/b/(\d+)"', e)
            page = page or re.search(r'href="[^"]*/i/\d+/(\d+)/cover', e)
            if not page:
                continue
            href = f"/b/{page.group(1)}/fb2"
            fb2 = True
        if href.startswith("/"):
            href = base + href
        content = re.search(r"<content[^>]*>(.*?)</content>", e, re.S)
        info = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_unescape(content.group(1)))) if content else ""
        translator = re.search(r"Перевод(?:чик)?:\s*(.+?)\s*(?:Год издания|Формат|Язык|Размер|Скачиваний|$)", info)
        year = re.search(r"Год издания:\s*(\d{4})", info)
        size = re.search(r"Размер:\s*(\d+)\s*[KК][bБ]", info)
        fmt = re.search(r"Формат:\s*([a-z0-9]+)", info, re.I)  # what the catalog holds; its "fb2" link may wrap an rtf
        hits.append(
            {
                "source": name,
                "title": title,
                "author": ", ".join(authors),
                "translator": translator.group(1).strip(" .,;") if translator else "",
                "year": year.group(1) if year else "",
                "size_kb": int(size.group(1)) if size else None,
                "lang": lang.group(1) if lang else "",
                "url": href,
                "kind": fmt.group(1).lower() if fmt else "fb2" if fb2 else "epub",
                "audio_url": "",
                "narrator": "",
                "readable": True,
            }
        )
    return hits


def search_opds(name: str, base: str, search_url: str, query: str) -> list[dict]:
    return opds_entries(name, base, _get(search_url + urllib.parse.quote(query)).decode("utf-8", "replace"))[:12]


AUTHOR_PAGES = 3  # 20 books per OPDS page


def author_books(name: str, base: str, query: str) -> tuple[str, list[dict]]:
    """(author name, books) when the catalog has an author whose name is exactly the query."""
    xml = _get(f"{base}/opds/search?searchType=authors&searchTerm=" + urllib.parse.quote(query)).decode(
        "utf-8", "replace"
    )
    want = norm_title(query)
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        title = html_unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip() if "<title>" in e else ""
        link = re.search(r'href="([^"]*/opds/author/\d+)"', e)
        if not link or not want or norm_title(title) != want:
            continue
        url = link.group(1)
        if url.startswith("/"):
            url = base + url
        books: list[dict] = []
        for page_url in (url + "/alphabet", url):  # flibusta lists books under /alphabet, coollib on the author page
            nxt: str | None = page_url
            for _ in range(AUTHOR_PAGES):
                if not nxt:
                    break
                page = _get(nxt).decode("utf-8", "replace")
                books += opds_entries(name, base, page)
                m = re.search(r'<link[^>]*href="([^"]+)"[^>]*rel="next"', page) or re.search(
                    r'<link[^>]*rel="next"[^>]*href="([^"]+)"', page
                )
                nxt = (base + m.group(1) if m and m.group(1).startswith("/") else m.group(1)) if m else None
            if books:
                break
        return title, books
    return "", []


def html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


def editions(hits: list[dict]) -> list[dict]:
    """One row per edition. Numbered volumes of the same edition (same source, work, author, translator)
    load together as one book; anything else stays a row of its own. Nothing is judged here."""
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
    order = {"fantasy-worlds": 0, "flibusta": 1, "coollib": 2}
    rows.sort(key=lambda r: (order.get(r["source"], 9), -int(r["year"] or 0), r["title"]))
    for r in rows:
        r.pop("url", None)
    return rows


def search_text(query: str) -> dict:
    """Every source in parallel: books by title, plus the books of an author named exactly like the
    query. Rows are editions as the catalogs describe them; the reader picks."""
    hits, by_author, errors = [], [], []
    author_name = ""
    want = norm_title(query)
    with ThreadPoolExecutor(max_workers=1 + 2 * len(OPDS_SOURCES)) as ex:
        futures = {ex.submit(search_fw, query): ("fantasy-worlds", "title")}
        for n, b, u in OPDS_SOURCES:
            futures[ex.submit(search_opds, n, b, u, query)] = (n, "title")
            futures[ex.submit(author_books, n, b, query)] = (n, "author")
        for f, (n, what) in futures.items():
            try:
                res = f.result(timeout=60)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{n}: {e}")
                continue
            if what == "author":
                name, books = res
                author_name = author_name or name
                by_author += books
            else:
                hits += res
    # fantasy-worlds also matches series names and authors: keep the hits that name the query in the
    # title, move the ones by an author named like the query to the author block
    if want:
        named = [
            h
            for h in hits
            if h["source"] != "fantasy-worlds" or want in norm_title(h["title"]) or norm_title(h["author"]) == want
        ]
        hits = named or hits
    fw_by_author = [h for h in hits if want and norm_title(h["author"]) == want]
    if fw_by_author:
        author_name = author_name or fw_by_author[0]["author"]
        by_author = fw_by_author + by_author
        hits = [h for h in hits if h not in fw_by_author]
    return {
        "hits": editions(hits)[:30],
        "author": {"name": author_name, "hits": editions(by_author)[:80]} if author_name else None,
        "errors": errors,
    }


_WHERE_CACHE: dict[str, tuple[float, float, dict, list]] = {}


def _book_and_timing(slug: str) -> tuple[dict, list]:
    """book.json and timing words, cached by file mtimes (the files are a few MB)."""
    d = BOOKS / slug
    bm = (d / "book.json").stat().st_mtime
    tm = (d / "timing.json").stat().st_mtime if (d / "timing.json").exists() else 0.0
    hit = _WHERE_CACHE.get(slug)
    if hit and hit[0] == bm and hit[1] == tm:
        return hit[2], hit[3]
    book = json.loads((d / "book.json").read_text(encoding="utf-8"))
    words = json.loads((d / "timing.json").read_text(encoding="utf-8"))["words"] if tm else []
    _WHERE_CACHE[slug] = (bm, tm, book, words)
    return book, words


def random_sentence(slug: str) -> dict:
    """A random mid-length sentence from a paragraph of the book (for finished books on the library page)."""
    import random

    book, _ = _book_and_timing(slug)
    paras = [b for b in book["blocks"] if b["kind"] == "p" and b.get("audio", True)]
    for _ in range(200):
        blk = random.choice(paras)
        if not blk["sentences"]:
            continue
        a, e = random.choice(blk["sentences"])
        text = blk["text"][a:e].strip()
        if 40 <= len(text) <= 160:
            return {
                "text": text,
                "chapter": book["chapters"][blk["chapter"]]["title"],
                "title": book.get("title", ""),
                "mode": "random",
            }
    blk = paras[0]
    return {
        "text": blk["text"][: blk["sentences"][0][1]] if blk["sentences"] else blk["text"][:120],
        "chapter": "",
        "title": book.get("title", ""),
        "mode": "random",
    }


def where_now(slug: str) -> dict:
    """The sentence the reader stopped at: by audio position for audio books, by sentence index otherwise."""
    st = load_state(slug)
    book, words = _book_and_timing(slug)
    blocks = book["blocks"]
    audio_mode = bool(words) and st.get("mode") != "pages"
    if audio_mode:
        pos = float(st.get("pos", 0) or 0)
        lo, hi = 0, len(words) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if words[mid][3] <= pos:
                lo = mid
            else:
                hi = mid - 1
        bi, cs = words[lo][0], words[lo][1]
        blk = blocks[bi]
        rng = next(
            (r for r in blk["sentences"] if r[0] <= cs < r[1]),
            blk["sentences"][-1] if blk["sentences"] else [0, len(blk["text"])],
        )
    else:
        target = int(st.get("sent", 0) or 0)
        n = 0
        bi, rng = 0, [0, 0]
        for i, blk in enumerate(blocks):
            if n + len(blk["sentences"]) > target:
                bi, rng = i, blk["sentences"][target - n]
                break
            n += len(blk["sentences"])
        else:
            bi, rng = len(blocks) - 1, blocks[-1]["sentences"][-1] if blocks and blocks[-1]["sentences"] else [0, 0]
    blk = blocks[bi]
    chapter = book["chapters"][blk["chapter"]]["title"] if book.get("chapters") else ""
    return {
        "text": blk["text"][rng[0] : rng[1]].strip(),
        "chapter": chapter,
        "title": book.get("title", ""),
        "mode": "audio" if audio_mode else "pages",
    }


def slug_from_source(urls: list[str]) -> str:
    """A slug for a book added by link without a title: site label plus the id from the URL."""
    if urls:
        u = urllib.parse.urlparse(urls[0])
        digits = sorted(re.findall(r"\d{2,}", u.path), key=len)  # the id, not the "2" of "fb2"
        host = u.hostname or ""
        label = "fw" if "fantasy-worlds" in host else host.split(".")[-2] if host.count(".") else "book"
        return re.sub(r"[^a-z0-9]+", "-", f"{label}-{digits[-1] if digits else int(time.time())}".lower()).strip("-")[
            :48
        ]
    return f"book-{int(time.time())}"


def slugify(title: str) -> str:
    s = "".join(TRANSLIT.get(c, c) for c in title.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48]
    return s or "book"


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """Return {field: {"value": str} | {"filename": str, "data": bytes}} from a multipart/form-data body."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    out: dict[str, dict] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fn = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if fn:
            out[name] = {"filename": fn, "data": payload}
        else:
            value = payload.decode("utf-8", "replace").strip()
            if name in out and "value" in out[name]:  # repeated field: keep every value (volumes / parts)
                out[name]["values"] = out[name].get("values", [out[name]["value"]]) + [value]
            else:
                out[name] = {"value": value}
    return out


def form_values(form: dict, key: str) -> list[str]:
    f = form.get(key) or {}
    vals = f.get("values") or ([f["value"]] if f.get("value") else [])
    out: list[str] = []
    for v in vals:
        out += [x.strip() for x in re.split(r"[\n,]+", v) if x.strip()]
    return out


def start_job(form: dict) -> tuple[dict | None, str]:
    """Save uploads, launch pipeline/add_book.py in the background. Returns (job info, error)."""
    val = lambda k: form.get(k, {}).get("value", "")  # noqa: E731
    title = val("title")
    slug = val("slug") or (slugify(title) if title else slug_from_source(form_values(form, "text_url")))
    if not SLUG_RE.match(slug):
        return None, "bad slug"
    d = BOOKS / slug
    if slug in JOBS and JOBS[slug]["proc"].poll() is None:
        return None, f"книга {slug} уже загружается"
    texts = form_values(form, "text_url")
    tf = form.get("text_file")
    has_file = bool(tf and tf.get("filename") and tf["data"])
    attach_audio = (d / "book.json").exists() and not texts and not has_file
    replace = (d / "book.json").exists() and not attach_audio and val("replace") == "1"
    if (d / "book.json").exists() and not attach_audio and not replace:
        return None, f"книга {slug} уже есть"
    d.mkdir(parents=True, exist_ok=True)
    if has_file:
        fname = "upload_" + re.sub(r"[^\w.-]+", "_", tf["filename"])
        (d / fname).write_bytes(tf["data"])
        texts.append(str(d / fname))
    if not texts and not attach_audio:
        return None, "нужен текст: ссылка или файл"

    def allowed(src: str) -> bool:
        if src.startswith(("http://", "https://")):
            return True
        try:
            return BOOKS.resolve() in Path(src).resolve().parents
        except OSError:
            return False

    if not all(allowed(t) for t in texts):
        return None, "ссылка должна начинаться с http(s)"
    audios = form_values(form, "audio_url")
    af = form.get("audio_file")
    if af and af.get("filename") and af["data"]:
        ext = os.path.splitext(af["filename"])[1].lower() or ".m4a"
        (d / ("upload" + ext)).write_bytes(af["data"])
        audios.append(str(d / ("upload" + ext)))
    if attach_audio and not audios:
        return None, "нужна ссылка на аудио или файл"
    if not all(allowed(a) for a in audios):
        return None, "ссылка на аудио должна начинаться с http(s)"
    py = str(PIPELINE_PY) if PIPELINE_PY.exists() else sys.executable
    cmd = [py, str(ROOT / "pipeline" / "add_book.py"), slug]
    for t in texts:
        cmd += ["--text", t]
    for a in audios:
        cmd += ["--audio", a]
    if val("align") != "on":
        cmd.append("--no-align")
    flags = (
        ("title", "--title"),
        ("author", "--author"),
        ("narrator", "--narrator"),
        ("translator", "--translator"),
        ("year", "--year"),
    )
    for k, flag in flags:
        if val(k):
            cmd += [flag, val(k)]
    if replace:  # new text, new sentence numbering: the page-mode position starts over (audio seconds stay valid)
        st = load_state(slug)
        for k in ("sent", "sentAt", "sentPct"):
            st.pop(k, None)
        save_state(slug, st)
    if not (d / "book.toml").exists():  # a stub so the card shows up as "loading" right away; add_book fills it in
        esc_ = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        stub = {"slug": slug, "title": title or slug, "author": val("author")}
        (d / "book.toml").write_text("".join(f'{k} = "{esc_(v)}"\n' for k, v in stub.items() if v), encoding="utf-8")
    with open(d / "add.log", "w", encoding="utf-8") as log:  # the child inherits the handle; ours closes here
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    JOBS[slug] = {"proc": proc, "started": time.time(), "slug": slug}
    return {"slug": slug}, ""


def job_status() -> dict:
    out = {}
    for slug, j in JOBS.items():
        code = j["proc"].poll()
        try:
            log = (BOOKS / slug / "add.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        lines = [ln for ln in log.splitlines() if ln.strip() and "warning" not in ln.lower()]
        # the last plain line, not the traceback frames: that is what the card shows
        tail = [ln for ln in lines if not ln.startswith(("  ", "Traceback", "+ ")) and "CalledProcessError" not in ln]
        lines = tail or lines
        out[slug] = {"running": code is None, "exit": code, "log": lines[-6:], "started": j["started"]}
    return out


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter log
        if args and "audio" in str(args[0]):
            return
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/books/"):
            rel = Path(path[len("/books/") :])
            target = (BOOKS / rel).resolve()
            if BOOKS.resolve() in target.parents:
                return str(target)
            return str(BOOKS / "__forbidden__")
        rel = path.lstrip("/") or "index.html"
        if rel == "favicon.ico":  # browsers ask for it regardless of <link rel=icon>
            rel = "favicon.png"
        target = (READER / rel).resolve()
        if target == READER.resolve() or READER.resolve() in target.parents:
            return str(target)
        return str(READER / "__forbidden__")

    def send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            self.close_connection = True  # do not try to resync the stream after a refused body
            raise ValueError("body too large")
        raw = self.rfile.read(n) or b"{}"
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ValueError(f"bad json: {e}") from None

    def read_form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 3_000_000_000:
            self.close_connection = True
            raise ValueError("body too large")
        return parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(n))

    def state_slug(self):
        m = re.match(r"^/api/state/([^/?]+)(/session)?$", self.path)
        if not m or not SLUG_RE.match(m.group(1)) or not (BOOKS / m.group(1)).is_dir():
            return None, None
        return m.group(1), bool(m.group(2))

    def do_GET(self):
        if self.path.startswith("/api/books"):
            return self.send_json(list_books())
        if self.path.startswith("/api/state/"):
            slug, _ = self.state_slug()
            if slug is None:
                return self.send_json({"error": "unknown book"}, HTTPStatus.NOT_FOUND)
            return self.send_json(load_state(slug))
        if self.path.startswith("/api/jobs"):
            return self.send_json(job_status())
        if self.path.startswith("/api/settings"):
            return self.send_json(load_settings())
        if self.path.startswith("/api/hits/"):
            slug = self.path.rsplit("/", 1)[-1]
            p = BOOKS / slug / "hits.json"
            if not SLUG_RE.match(slug) or not p.exists():
                return self.send_json({"hits": [], "author_hits": None})
            return self.send_json(json.loads(p.read_text(encoding="utf-8")))
        if self.path.startswith("/api/wishlist"):
            return self.send_json(load_wishlist())
        if self.path.startswith("/api/where/"):
            slug = self.path.rsplit("/", 1)[-1].split("?")[0]
            if not SLUG_RE.match(slug) or not (BOOKS / slug / "book.json").exists():
                return self.send_json({"error": "unknown book"}, HTTPStatus.NOT_FOUND)
            try:
                if "random=1" in self.path:
                    return self.send_json(random_sentence(slug))
                return self.send_json(where_now(slug))
            except Exception as e:  # noqa: BLE001
                return self.send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        if self.path.startswith("/api/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0].strip()
            if not q:
                return self.send_json([])
            try:
                return self.send_json(search_text(q))
            except Exception as e:  # noqa: BLE001 - upstream site down or changed: report, don't crash
                return self.send_json({"error": f"поиск недоступен: {e}"}, HTTPStatus.BAD_GATEWAY)
        path = self.translate_path(self.path)
        if os.path.isfile(path) and "Range" in self.headers:
            return self.send_range(path)
        return super().do_GET()

    # The request body is always read before responding: on a keep-alive connection an unread
    # body would be parsed as the start of the next request.
    def do_PUT(self):
        try:
            body = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/settings"):
            return self.send_json(merge_settings(body))
        if self.path.startswith("/api/wishlist/"):
            return self.send_json(wishlist_update(self.path.rsplit("/", 1)[-1], body))
        if self.path.startswith("/api/hits/"):
            try:
                save_hits(self.path.rsplit("/", 1)[-1], body)
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        slug, is_session = self.state_slug()
        if slug is None or is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return self.send_json(merge_state(slug, body))

    def do_POST(self):
        if self.path.startswith("/api/add"):
            try:
                form = self.read_form()
                with STATE_LOCK:
                    job, err = start_job(form)
            except Exception as e:  # noqa: BLE001 - malformed multipart, bad paths, disk errors: report, don't crash
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            if err:
                return self.send_json({"error": err}, HTTPStatus.BAD_REQUEST)
            return self.send_json(job)
        try:
            delta = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/wishlist"):
            try:
                return self.send_json(wishlist_add(delta))
            except ValueError as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        slug, is_session = self.state_slug()
        if slug is None or not is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return self.send_json(add_session(slug, delta))

    def do_DELETE(self):
        if self.path.startswith("/api/wishlist/"):
            return self.send_json(wishlist_delete(self.path.rsplit("/", 1)[-1]))
        if self.path.startswith("/api/books/"):
            try:
                delete_book(self.path.rsplit("/", 1)[-1])
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        if self.path.endswith((".json", ".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_range(self, path: str):
        size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers["Range"])
        if not m or not (m.group(1) or m.group(2)):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        ctype = self.guess_type(path)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"readsync: http://{args.host}:{args.port}/  (books: {', '.join(b['slug'] for b in list_books()) or 'none'})")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
