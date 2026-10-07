"""Everything about the books on disk: listing, reading state, settings, saved titles, background
pipeline jobs, and the "where am I" line. No HTTP here; serve.py routes to these functions."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse
from pathlib import Path

import state
from sources.audio import BadRef, parse_ref

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


def _pipeline() -> None:
    """The pipeline's own modules (manifest, tidy) importable from here."""
    if str(ROOT / "pipeline") not in sys.path:
        sys.path.insert(0, str(ROOT / "pipeline"))


def sweep_jobs() -> None:
    """Work dirs no live job holds are what a killed job, a crash or a power cut left: they go, and the
    book they were for is tidied. A job still running (here, from an earlier server or from the
    command line) keeps its own."""
    _pipeline()
    from tidy import held, tidy, work_root

    root = work_root()
    if not root.is_dir():
        return
    for w in root.iterdir():
        job = JOBS.get(w.name)
        if (job and job["proc"].poll() is None) or held(w):
            continue
        try:
            shutil.rmtree(w)
            if SLUG_RE.match(w.name) and (BOOKS / w.name / "book.toml").is_file():
                tidy(BOOKS / w.name)
        except OSError as e:
            print(f"work dir {w.name} not swept: {e}", file=sys.stderr, flush=True)


def ensure_manifests() -> None:
    """Books made before manifests existed get one, once: a phone cannot tell a finished copy of them
    from a half-synced one otherwise. Books still loading are left to their job, and so are books
    whose last job failed (their add.log stays until a job succeeds). One unreadable book never
    keeps the server from starting. Runs at server start, so it sweeps what dead jobs left first.
    Books stamped before the end of their main text was get only that added (see manifest.stamp_ends)."""
    sweep_jobs()
    _pipeline()
    from manifest import ID_RE, stamp, stamp_ends

    for toml in BOOKS.glob("*/book.toml"):
        d = toml.parent
        try:
            if not (d / "book.json").exists() or (d / "add.log").exists() or d.name in JOBS:
                continue
            text = toml.read_text(encoding="utf-8")
            if not ID_RE.search(text):
                stamp(d)
            elif not re.search(r"(?m)^text_end\s*=", text):
                stamp_ends(d)
        except (OSError, ValueError) as e:
            print(f"manifest skipped for {d.name}: {e}", file=sys.stderr, flush=True)


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
        meta["building"] = job_running(d.name)
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
        # an audiobook read as pages moves its page, not its narrator: the mode it was left in says which counts,
        # as on the phone. At the end: within a minute of it, or the last spread (what read as finished before
        # statuses existed)
        by_page = duration == 0 or st.get("mode") == "pages"
        at_end = (st.get("sentPct") or 0) >= 99 if by_page else pos >= duration - 60
        days = st.get("finished")
        meta["state"] = {
            "opened": st.get("opened", 0),
            "shelf": st.get("shelf", ""),
            "pos": pos,
            "duration": duration,
            "sent": st.get("sent", 0),
            "sentPct": st.get("sentPct", 0),
            "mode": st.get("mode", ""),
            "seconds": sum(v.get("sec", 0) for v in (st.get("stats") or {}).get("days", {}).values()),
            "atEnd": bool(at_end),
            "finished": [x for x in days if isinstance(x, str)] if isinstance(days, list) else [],
            **state.status(st, duration > 0, bool(at_end)),
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


# `slug`: the book a picked edition is loading into. The title stays until that job succeeds, so a
# load called off or failed leaves the card «без текста» with its editions, as it was
WISH_FIELDS = ("title", "author", "note", "text_url", "audio_url", "searched", "query", "slug")
WISH_JSON = ("hits", "author_hits", "unopenable")  # the last search result stays with the title until it is loaded


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


def wishlist_loaded(slug: str) -> None:
    """The book a saved title was loading into is there: the title has done its job."""
    with STATE_LOCK:
        items = load_wishlist()
        keep = [i for i in items if i.get("slug") != slug]
        if len(keep) != len(items):
            save_wishlist(keep)


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
                "unopenable": int(data.get("unopenable") or 0),  # found, but only in formats that do not open
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
    if job_running(slug):
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
        return None, "не вышло назвать папку книги"
    audio_ref = val("audio_ref")
    if audio_ref:  # a recording found by the audio search: the pipeline resolves its parts itself
        try:
            parse_ref(audio_ref)
        except BadRef as e:
            return None, str(e)
        if form_values(form, "audio_url") or (form.get("audio_file") or {}).get("filename"):
            return None, "либо найденная озвучка, либо своя ссылка или файл"
    d = BOOKS / slug
    if job_running(slug):
        return None, f"«{title_of(slug)}» уже загружается"
    texts = form_values(form, "text_url")
    tf = form.get("text_file")
    has_file = bool(tf and tf.get("filename") and tf["data"])
    attach_audio = (d / "book.json").exists() and not texts and not has_file
    replace = (d / "book.json").exists() and not attach_audio and val("replace") == "1"
    if (d / "book.json").exists() and not attach_audio and not replace:
        return None, f"«{title_of(slug)}» уже есть в библиотеке"
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
    if attach_audio and not audios and not audio_ref:
        return None, "нужна ссылка на аудио или файл"
    if not all(allowed(a) for a in audios):
        return None, "ссылка на аудио должна начинаться с http(s)"
    py = str(PIPELINE_PY) if PIPELINE_PY.exists() else sys.executable
    cmd = [py, str(ROOT / "pipeline" / "add_book.py"), slug]
    for t in texts:
        cmd += ["--text", t]
    for a in audios:
        cmd += ["--audio", a]
    if audio_ref:
        cmd += ["--audio-ref", audio_ref]
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
        # no title of our own for a link: add_book keeps a title it finds here, and the book's is the one wanted
        stub = {"slug": slug, "title": title, "author": val("author")}
        (d / "book.toml").write_text("".join(f'{k} = "{esc_(v)}"\n' for k, v in stub.items() if v), encoding="utf-8")
    launch(slug, cmd)
    return {"slug": slug}, ""


# the job's exit code, written at the end of its log by the shell that runs it: the server may be gone (an
# update, a restart, the iCloud switch) when the job ends, and the card must still know how it went
EXIT_MARK = "readsync: exit "
EXIT_RE = re.compile(r"readsync: exit (\d+)")


def launch(slug: str, cmd: list[str]) -> None:
    """A job runs in a process group of its own, so a stop reaches yt-dlp, ffmpeg and the aligner too.
    It outlives the server; its log (add.log) stays until it succeeds or the reader dismisses the failure."""
    wrapped = ["/bin/sh", "-c", f'"$@"; code=$?; echo "{EXIT_MARK}$code"; exit $code', "sh", *cmd]
    with open(BOOKS / slug / "add.log", "w", encoding="utf-8") as log:  # the child inherits it; ours closes here
        proc = subprocess.Popen(wrapped, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, start_new_session=True)
    JOBS[slug] = {"proc": proc, "started": time.time(), "slug": slug}


def _holder(slug: str) -> int | None:
    """The pid of a live job that claimed this book's work dir, whichever server (or shell) started it."""
    if not SLUG_RE.match(slug):
        return None
    _pipeline()
    from tidy import held, work_dir

    w = work_dir(slug)
    if not held(w):
        return None
    try:
        return int((w / "pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def job_running(slug: str) -> bool:
    """A job is loading this book: one this server started, or one an earlier server left running."""
    job = JOBS.get(slug)
    if job and job["proc"].poll() is None:
        return True
    return _holder(slug) is not None


def title_of(slug: str) -> str:
    """The book's title for a message, its folder name when it has none yet."""
    try:
        return tomllib.loads((BOOKS / slug / "book.toml").read_text(encoding="utf-8")).get("title") or slug
    except (OSError, ValueError):
        return slug


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(pgid: int, proc: subprocess.Popen | None = None, grace: float = 10.0) -> None:
    """SIGTERM to the whole group (the job unwinds and tidies), SIGKILL to whatever is left after `grace`.
    `proc` is the leader when this server started the job, to reap it."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and ((proc is not None and proc.poll() is None) or _group_alive(pgid)):
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError, PermissionError):  # EPERM: only an unreaped leader is left
        os.killpg(pgid, signal.SIGKILL)
    if proc is not None:
        proc.wait()


def stop_job(slug: str) -> None:
    """Called off by the reader: the pipeline is stopped and what it half-downloaded is thrown away.
    A book that was already there keeps its text; a new one goes altogether, so the title it was loading
    for is «без текста» again. A job that is over has nothing to stop: its failure is dismissed (the log goes)."""
    if not SLUG_RE.match(slug):
        raise ValueError("нечего останавливать")
    d = BOOKS / slug
    job = JOBS.get(slug)
    pid = _holder(slug)
    if job and job["proc"].poll() is None:
        kill_group(job["proc"].pid, job["proc"])  # start_new_session: the job leads its own group
    elif pid is not None:  # started by an earlier server: its group is found from the pid it left
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            pgid = 0
        if pgid and pgid != os.getpgrp():
            kill_group(pgid)
    elif (d / "add.log").exists():
        JOBS.pop(slug, None)
        (d / "add.log").unlink()
        return
    else:
        raise ValueError("нечего останавливать")
    JOBS.pop(slug, None)  # called off on purpose: the card must not report it as a failure
    _pipeline()
    from tidy import tidy, work_dir

    shutil.rmtree(work_dir(slug), ignore_errors=True)
    if not (d / "book.json").exists():
        shutil.rmtree(d, ignore_errors=True)
    elif d.is_dir():
        (d / "add.log").unlink(missing_ok=True)
        tidy(d)


def start_align(slug: str) -> tuple[dict | None, str]:
    """Precise MMS word alignment for a book that already has audio; long, low priority, in the background."""
    d = BOOKS / slug
    if not SLUG_RE.match(slug) or not (d / "timing.json").exists():
        return None, "у книги нет аудио"
    if job_running(slug):
        return None, "книга ещё загружается"
    py = str(PIPELINE_PY) if PIPELINE_PY.exists() else sys.executable
    launch(slug, [py, str(ROOT / "pipeline" / "align.py"), str(d)])
    return {"slug": slug}, ""


def job_status() -> dict:
    """Every job the library knows: running (here or left by an earlier server), or over with its log
    still on disk. The log is the record: a restart forgets neither a running job nor why one failed.
    `stage` is the step a running job is at, as the pipeline says it («часть 3/12», «размечаю»)."""
    out = {}
    for slug in sorted(set(JOBS) | {p.parent.name for p in BOOKS.glob("*/add.log")}):
        if not SLUG_RE.match(slug):
            continue
        j = JOBS.get(slug)
        path = BOOKS / slug / "add.log"
        try:
            log = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        raw = [ln for ln in log.splitlines() if ln.strip()]
        marked = next((int(m.group(1)) for ln in reversed(raw) if (m := EXIT_RE.fullmatch(ln.strip()))), None)
        code = j["proc"].poll() if j else None
        running = (j is not None and code is None) or _holder(slug) is not None
        if running:
            code = None
        elif code is None:
            code = marked if marked is not None else -1  # no code at all: the job died with the Mac or was killed
        lines = [ln for ln in raw if "warning" not in ln.lower() and not ln.startswith(EXIT_MARK)]
        # the last plain line, not the traceback frames: that is what the card shows
        tail = [ln for ln in lines if not ln.startswith(("  ", "Traceback", "+ ")) and "CalledProcessError" not in ln]
        lines = tail or lines
        if code == -1:
            lines = [*lines[:-1], f"оборвалась: {lines[-1]}"] if lines else ["загрузка оборвалась"]
        started = j["started"] if j else (path.stat().st_mtime if log else 0)
        if code == 0 and log:  # a finished job has nothing left to say: its log goes, and the title it was for
            path.unlink(missing_ok=True)
            wishlist_loaded(slug)
        stage = next((ln for ln in reversed(lines) if re.search("[А-Яа-яЁё]", ln)), "") if running else ""
        out[slug] = {"running": running, "exit": code, "log": lines[-6:], "stage": stage, "started": started}
    return out
