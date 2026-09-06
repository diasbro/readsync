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


WISH_FIELDS = ("title", "author", "note", "text_url", "audio_url")


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
        save_wishlist(items)
        return items


def wishlist_delete(wid: str) -> list[dict]:
    with STATE_LOCK:
        items = [i for i in load_wishlist() if i["id"] != wid]
        save_wishlist(items)
        return items


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
                "year": b.get("year") or "",
                "lang": b.get("lang_code") or "",
                "series": b.get("series_name") or "",
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


def search_opds(name: str, base: str, search_url: str, query: str) -> list[dict]:
    xml = _get(search_url + urllib.parse.quote(query)).decode("utf-8", "replace")
    hits = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S)[:12]:
        title = (
            html_unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip()
            if re.search(r"<title>", e)
            else ""
        )
        authors = [html_unescape(x).strip() for x in re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)]
        lang = re.search(r"<dc:language>(.*?)</dc:language>", e)
        fb2 = re.search(r'href="([^"]+)"[^>]*type="application/fb2\+zip"', e) or re.search(
            r'type="application/fb2\+zip"[^>]*href="([^"]+)"', e
        )
        epub = re.search(r'href="([^"]+)"[^>]*type="application/epub\+zip"', e)
        link = fb2 or epub
        if link:
            href = link.group(1)
        else:  # some catalogs (coollib) only link the book page from search results: derive /b/<id>/fb2
            page = re.search(r'href="(?:https?://[^/"]+)?/b/(\d+)"', e)
            if not page:
                continue
            href = f"/b/{page.group(1)}/fb2"
            fb2 = True
        if href.startswith("/"):
            href = base + href
        hits.append(
            {
                "source": name,
                "id": href,
                "title": title,
                "author": ", ".join(authors),
                "year": "",
                "lang": lang.group(1) if lang else "",
                "series": "",
                "url": href,
                "kind": "fb2" if fb2 else "epub",
                "audio_url": "",
                "narrator": "",
                "readable": True,
            }
        )
    return hits


def html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


def search_text(query: str) -> dict:
    """Query every source in parallel; group volumes of the same work so they load as one book.
    Ranking: exact and complete first, then source priority (fantasy-worlds, flibusta, coollib)."""
    hits, errors = [], []
    with ThreadPoolExecutor(max_workers=1 + len(OPDS_SOURCES)) as ex:
        futures = {ex.submit(search_fw, query): "fantasy-worlds"}
        futures.update({ex.submit(search_opds, n, b, u, query): n for n, b, u in OPDS_SOURCES})
        for f, n in futures.items():
            try:
                hits += f.result(timeout=45)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{n}: {e}")
    want = norm_title(query)
    groups: dict[tuple, dict] = {}
    for h in hits:
        key = (h["source"], norm_title(h["title"]), norm_title(h["author"]))
        g = groups.setdefault(
            key,
            {
                "source": h["source"],
                "title": h["title"],
                "author": h["author"],
                "year": h["year"],
                "lang": h["lang"],
                "series": h["series"],
                "audio_url": h["audio_url"],
                "narrator": h["narrator"],
                "readable": h["readable"],
                "parts": [],
            },
        )
        g["parts"].append({"title": h["title"], "url": h["url"], "kind": h["kind"], "no": volume_no(h["title"])})
        g["readable"] = g["readable"] or h["readable"]
    out = []
    for g in groups.values():
        singles = [p for p in g["parts"] if not p["no"]]
        volumes = [p for p in g["parts"] if p["no"]]
        if singles:  # a complete one-file edition beats volumes; several editions: keep the first
            g["parts"] = [singles[0]]
            g["title"] = singles[0]["title"]
            g["complete"] = True
        else:
            seen: set = set()  # several editions of the same volume: keep the first
            vols = [p for p in sorted(volumes, key=lambda p: p["no"]) if not (p["no"] in seen or seen.add(p["no"]))]
            g["parts"] = vols
            g["title"] = re.sub(r"[\s.,:;(–—-]+$", "", VOLUME_RE.split(vols[0]["title"])[0]).strip() or g["title"]
            g["complete"] = [p["no"] for p in vols] == list(range(1, len(vols) + 1)) and (
                len(vols) > 1 or vols[0]["no"] == 1
            )
        t = norm_title(g["title"])
        g["exact"] = bool(want) and (
            t == want or norm_title(f"{g['author']} {g['title']}") == want or t.startswith(want + " ")
        )
        out.append(g)
    order = {"fantasy-worlds": 0, "flibusta": 1, "coollib": 2}
    out.sort(
        key=lambda g: (not (g["exact"] and g["complete"] and g["readable"]), not g["exact"], order.get(g["source"], 9))
    )
    return {"hits": out[:12], "errors": errors}


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
    if (d / "book.json").exists() and not attach_audio:
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
    for k, flag in (("title", "--title"), ("author", "--author"), ("narrator", "--narrator")):
        if val(k):
            cmd += [flag, val(k)]
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
