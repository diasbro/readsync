"""Everything about the books on disk: listing, reading state, settings, saved titles, background
pipeline jobs, and the "where am I" line. No HTTP here; serve.py routes to these functions."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
from pathlib import Path

import state

ROOT = Path(__file__).resolve().parent
READER = ROOT / "reader"
# books live next to the code when run from a checkout, and outside it when the Mac app runs:
# the app updates its code in place, so nothing of the reader's may sit inside it
# one library per Mac: the menu-bar app's (which may live in iCloud), else this checkout's own books/
APP_BOOKS = Path.home() / "Library" / "Application Support" / "readsync" / "books"
BOOKS = Path(os.environ.get("READSYNC_BOOKS") or (APP_BOOKS if APP_BOOKS.exists() else ROOT / "books")).expanduser()
STATE_LOCK = threading.Lock()
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JOBS: dict[str, dict] = {}
PIPELINE_PY = Path(os.environ.get("READSYNC_PYTHON") or ROOT / ".venv" / "bin" / "python").expanduser()
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


def ensure_manifests() -> None:
    """Books made before manifests existed get one, once: a phone cannot tell a finished copy of them
    from a half-synced one otherwise. Books still loading are left to their job."""
    sys.path.insert(0, str(ROOT / "pipeline"))
    from manifest import stamp

    for toml in BOOKS.glob("*/book.toml"):
        d = toml.parent
        if (
            (d / "book.json").exists()
            and d.name not in JOBS
            and not re.search(r"(?m)^id\s*=", toml.read_text(encoding="utf-8"))
        ):
            stamp(d)


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
    """The reading state every device has written for this book, merged (see state.py)."""
    if not SLUG_RE.match(slug):
        return {}
    return state.load(BOOKS / slug)


def merge_state(slug: str, patch: dict) -> dict:
    if not SLUG_RE.match(slug) or not (BOOKS / slug).is_dir():
        raise ValueError("unknown book")
    return state.put(BOOKS / slug, patch)


def add_session(slug: str, delta: dict) -> dict:
    if not SLUG_RE.match(slug) or not (BOOKS / slug).is_dir():
        raise ValueError("unknown book")
    try:
        sec, words = float(delta.get("sec", 0)), float(delta.get("words", 0))
    except (TypeError, ValueError):
        sec, words = 0.0, 0.0
    return state.add_session(BOOKS / slug, str(delta.get("day", ""))[:10], sec, words)


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


WISH_FIELDS = ("title", "author", "note", "text_url", "audio_url", "searched", "query")
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
                        v = str(data[k] or "").strip()
                        if k == "title" and not v:  # a title is the only thing a shell card has
                            continue
                        it[k] = v
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
        json.dumps(
            {
                "hits": data.get("hits") or [],
                "author_hits": data.get("author_hits"),
                "query": str(data.get("query") or ""),  # what was asked for, to search again from
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def rename_book(slug: str, title: str) -> dict:
    """Rename a book in place. Only the title line of book.toml is rewritten: the slug, the files,
    the reading state and every other field stay as they are."""
    d = BOOKS / slug
    title = " ".join(str(title or "").split())
    if not SLUG_RE.match(slug) or not (d / "book.toml").is_file():
        raise ValueError("unknown book")
    if not title:
        raise ValueError("нужно название")
    text = (d / "book.toml").read_text(encoding="utf-8")
    line = 'title = "{}"'.format(title.replace("\\", "\\\\").replace('"', '\\"'))
    new_text, hits = re.subn(r"(?m)^title\s*=.*$", lambda _: line, text, count=1)
    if not hits:
        new_text = line + "\n" + text
    tmp = d / "book.toml.tmp"
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, d / "book.toml")
    forget_query(slug)
    return {"slug": slug, "title": title}


def forget_query(slug: str) -> None:
    """A renamed book keeps the editions it was picked from — another one may still be worth loading —
    but not the query that found them: the search field offers the new name instead."""
    f = BOOKS / slug / "hits.json"
    try:
        saved = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not saved.get("query"):
        return
    saved["query"] = ""
    f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")


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
        state.forget_sent(d)
    if not (d / "book.toml").exists():  # a stub so the card shows up as "loading" right away; add_book fills it in
        esc_ = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        stub = {"slug": slug, "title": title or slug, "author": val("author")}
        (d / "book.toml").write_text("".join(f'{k} = "{esc_(v)}"\n' for k, v in stub.items() if v), encoding="utf-8")
    with open(d / "add.log", "w", encoding="utf-8") as log:  # the child inherits the handle; ours closes here
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    JOBS[slug] = {"proc": proc, "started": time.time(), "slug": slug}
    return {"slug": slug}, ""


def stop_job(slug: str) -> None:
    """Called off by the reader: the pipeline is stopped and what it half-downloaded is thrown away.
    A book that was already there keeps its text; a new one keeps its card, with no text yet."""
    job = JOBS.get(slug)
    if not SLUG_RE.match(slug) or not job or job["proc"].poll() is not None:
        raise ValueError("нечего останавливать")
    job["proc"].terminate()
    try:
        job["proc"].wait(timeout=10)
    except subprocess.TimeoutExpired:
        job["proc"].kill()
    JOBS.pop(slug, None)  # called off on purpose: the card must not report it as a failure
    d = BOOKS / slug
    shutil.rmtree(d / "parts", ignore_errors=True)
    for leftover in d.glob("upload_*"):
        leftover.unlink(missing_ok=True)


def start_align(slug: str) -> tuple[dict | None, str]:
    """Precise MMS word alignment for a book that already has audio; long, low priority, in the background."""
    d = BOOKS / slug
    if not SLUG_RE.match(slug) or not (d / "timing.json").exists():
        return None, "у книги нет аудио"
    if slug in JOBS and JOBS[slug]["proc"].poll() is None:
        return None, "книга ещё загружается"
    py = str(PIPELINE_PY) if PIPELINE_PY.exists() else sys.executable
    with open(d / "add.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [py, str(ROOT / "pipeline" / "align.py"), str(d)], stdout=log, stderr=subprocess.STDOUT, cwd=ROOT
        )
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
        if code == 0:  # a finished job has nothing left to say: its log goes with it
            (BOOKS / slug / "add.log").unlink(missing_ok=True)
        out[slug] = {"running": code is None, "exit": code, "log": lines[-6:], "started": j["started"]}
    return out
