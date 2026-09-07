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
        out[slug] = {"running": code is None, "exit": code, "log": lines[-6:], "started": j["started"]}
    return out
