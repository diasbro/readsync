#!/usr/bin/env python3
"""Add a book to readsync, or add audio to an existing one.

Usage:
  python pipeline/add_book.py <slug> --text <url|file> [--text <url|file> ...] [--audio <url|file> ...]
      [--title T] [--author A] [--narrator N] [--lang ru] [--no-align] [--whisper-model small]
  python pipeline/add_book.py <slug> --audio <url|file> [...]      # attach audio to an existing text-only book
  python pipeline/add_book.py <slug> --audio-ref <ref> [--narrator N]   # a recording the audio search found

Text sources: fantasy-worlds reader pages, any HTML page, FB2 / FB2.zip, EPUB, TXT (local files or
direct download links). Several --text values are volumes of one book and are merged in order.
Audio sources: YouTube URLs or local files; several --audio values are parts and are joined in order.
--audio-ref names a recording of sources.audio (knigavuhe, YouTube, archive.org): its parts are resolved
right before downloading, since their links expire; mp3 parts are fetched one at a time with resume.
Without captions the audio is transcribed with faster-whisper for coarse anchoring.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compact import aac_args  # noqa: E402
from extract_text import MAX_UNPACKED, safe_name  # noqa: E402
from manifest import clean_title, stamp, toml_str  # noqa: E402
from tidy import PLAYABLE, land, take, tidy, work_dir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# the same root the server uses: the Mac app keeps the books outside the code it updates
# one library per Mac: the menu-bar app's (which may live in iCloud), else this checkout's own books/
APP_BOOKS = Path.home() / "Library" / "Application Support" / "readsync" / "books"
BOOKS = Path(os.environ.get("READSYNC_BOOKS") or (APP_BOOKS if APP_BOOKS.exists() else ROOT / "books")).expanduser()
PY = sys.executable
PIPE = ROOT / "pipeline"
UA = {"User-Agent": "Mozilla/5.0"}
# what a recording costs on disk while it is built, per second of audio: the source (mp3 at 128 kbit/s
# where its size is unknown), our AAC-LC mono 48 kbit/s copy, and the 16 kHz mono WAV for the timing
SOURCE_BPS = 128_000 // 8
OURS_BPS = 48_000 // 8
WAV_BPS = 16_000 * 2


def say(msg: str) -> None:
    """A step of the job for its log (stdout and stderr both go there), apart from the commands echoed."""
    print(msg, file=sys.stderr, flush=True)


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def unwrap(data: bytes, hint: str) -> tuple[bytes, str]:
    """A catalog may pack one file in a zip, and that zip in another («book.pdf.zip» inside a zip): a
    single-file archive is opened, twice at most, so what is inside gets sniffed. An epub is itself a
    zip and an fb2.zip is read as it is; both stay packed."""
    for _ in range(2):
        if data[:2] != b"PK":
            break
        try:
            z = zipfile.ZipFile(__import__("io").BytesIO(data))
            # an .fbd beside a pdf is the catalog's description of it, not a second book
            files = [i for i in z.infolist() if not i.is_dir() and not i.filename.lower().endswith(".fbd")]
        except zipfile.BadZipFile:
            break
        names = [i.filename for i in files]
        if "META-INF/container.xml" in names or len(files) != 1 or names[0].lower().endswith(".fb2"):
            break
        if files[0].file_size > MAX_UNPACKED:
            raise SystemExit(f"в архиве файл на {files[0].file_size / 1e6:.0f} МБ: это не книга")
        data, hint = z.read(files[0]), names[0]
    return data, hint


def sniff(data: bytes, hint: str) -> str:
    """Return one of html, fb2, fb2zip, epub, pdf, txt."""
    head = data[:4096].lstrip()
    hint = hint.lower()
    if head[:5] == b"%PDF-" or hint.endswith(".pdf"):
        return "pdf"
    if data[:2] == b"PK":
        try:
            names = zipfile.ZipFile(__import__("io").BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            names = []
        if "META-INF/container.xml" in names or hint.endswith(".epub"):
            return "epub"
        if names and not any(n.lower().endswith(".fb2") for n in names):
            exts = ", ".join(sorted({n.rsplit(".", 1)[-1].lower() for n in names if "." in n}))
            raise SystemExit(f"в архиве {exts}; поддерживаются fb2, epub, pdf, txt, html")
        return "fb2zip"
    if b"<FictionBook" in head or hint.endswith(".fb2"):
        return "fb2"
    if re.search(rb"<(html|!doctype html|body|p)\b", head, re.I) or hint.endswith((".html", ".htm")):
        return "html"
    return "txt"


def fetch_text(src: str, part_dir: Path) -> str:
    """Download or copy one text source into part_dir; return the extractor kind. A download cut short
    resumes (see `download`)."""
    part_dir.mkdir(parents=True, exist_ok=True)
    if is_url(src):
        got = part_dir / "source"
        final = download(src, got, what="текст", timeout=120)
        data = got.read_bytes()
        got.unlink()
        data, final = unwrap(data, final)
        kind = sniff(data, final)
    else:
        p = Path(src).expanduser()
        data, final = unwrap(p.read_bytes(), p.name)
        kind = sniff(data, final)
    name = {
        "html": "book.html",
        "fb2": "book.fb2",
        "fb2zip": "book.fb2.zip",
        "epub": "book.epub",
        "pdf": "book.pdf",
        "txt": "book.txt",
    }[kind]
    (part_dir / name).write_bytes(data)
    if kind == "html" and is_url(src):
        download_site_images(data, src, part_dir)
    return kind


def download_site_images(data: bytes, src: str, d: Path) -> None:
    """fantasy-worlds reader pages reference illustrations as data-src under /book/<id>/images/."""
    names = sorted(set(re.findall(rb'data-src="([^"]+)"', data)))
    if not names or b'id="book-content"' not in data:
        return
    (d / "images").mkdir(exist_ok=True)
    base = src.rsplit("/", 1)[0]
    for name in names:
        n = name.decode()
        if not safe_name(n):
            continue
        try:
            r = urllib.request.Request(f"{base}/images/{n}", headers=UA)
            (d / "images" / safe_name(n)).write_bytes(urllib.request.urlopen(r, timeout=60).read())
        except (OSError, ValueError):
            print("image not downloaded:", n, flush=True)


# what a catalog serves instead of a book when the rights holder complained or the file is gone
STUB_RE = re.compile(
    r"книга (заблокирована|удалена|не найдена)|удалена по требованию|доступ к книге ограничен"
    r"|страница не найдена|book (is )?blocked|not found",
    re.I,
)
MIN_WORDS = 500  # below this it is a notice, not a book: the shortest classics still run into thousands


def check_real_book(book: dict, parts: int) -> None:
    """A downloaded file that turned out to be a stub page stops the load, so no such book is made.
    The reader is told what happened and can take another edition."""
    words = sum(len(b["text"].split()) for b in book["blocks"])
    head = " ".join(b["text"] for b in book["blocks"][:8])
    if STUB_RE.search(head) and words < 3000:
        first = (book["blocks"][0]["text"] if book["blocks"] else "").strip()[:80]
        raise SystemExit(f"на сайте вместо книги заглушка: «{first}». Возьми другое издание")
    if parts == 1 and words < MIN_WORDS:
        raise SystemExit(f"в файле всего {words} слов, это не книга. Возьми другое издание")


EXTRACTORS = {
    "html": "extract_text.py",
    "fb2": "extract_fb2.py",
    "fb2zip": "extract_fb2.py",
    "epub": "extract_epub.py",
    "pdf": "extract_pdf.py",
    "txt": "extract_txt.py",
}


def build_text(sources: list[str], d: Path, title: str, author: str, w: Path) -> None:
    """Download, extract and merge the text in the work dir `w`: book.json and images/ wait there until
    the whole job is done (see land_text). A folder made and deleted inside the library while iCloud
    uploads it comes back as an empty placeholder."""
    parts_dir = w / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    for i, src in enumerate(sources, 1):
        part = parts_dir / f"{i:02d}"
        kind = fetch_text(src, part)
        run([PY, str(PIPE / EXTRACTORS[kind]), str(part)])
        check_real_book(json.loads((part / "book.json").read_text(encoding="utf-8")), len(sources))
    # merge parts (a single part is copied through), then gather their images beside the merged book
    cmd = [PY, str(PIPE / "merge_books.py"), str(w), "--title", title, "--author", author]
    run(cmd)
    (w / "images").mkdir(exist_ok=True)
    for part in sorted(parts_dir.iterdir()):
        if (part / "images").is_dir():
            for f in (part / "images").iterdir():
                shutil.copy(f, w / "images" / f.name)
    shutil.rmtree(parts_dir)  # the downloads served their purpose: the merged book is all that is read


def fetch_audio(src: str, w: Path, idx: int, lang: str) -> Path:
    """Download one audio part (YouTube via yt-dlp, with auto captions) or copy a local file, into the
    work dir."""
    if is_url(src):
        out = f"part{idx:02d}.%(ext)s"
        run(
            [
                "yt-dlp",
                "-f",
                "bestaudio[ext=webm]/bestaudio",
                "--write-auto-subs",
                "--sub-langs",
                f"{lang}-orig,{lang}",
                "--sub-format",
                "json3",
                "--no-progress",
                "-o",
                out,
                src,
            ],
            cwd=w,
        )
        return next(f for f in w.iterdir() if f.stem == f"part{idx:02d}" and f.suffix not in (".json3", ".part"))
    p = Path(src).expanduser()
    dst = w / f"part{idx:02d}{p.suffix.lower()}"
    if not dst.exists():
        shutil.copy(p, dst)
    return dst


def duration_of(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out or 0)


def recording(ref: str) -> tuple[str, list[dict]]:
    """The kind of a recording ref and its parts as they are now: [{title, duration, url, size}]."""
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))
    from sources import audio

    kind, _ = audio.parse_ref(ref)
    return kind, audio.parts(ref)


def check_space(parts: list[dict], w: Path) -> None:
    """Stop before the first byte if the parts, our copy and the WAV would not fit."""
    need = 0.0
    for p in parts:
        dur = float(p.get("duration") or 0)
        need += (p.get("size") or dur * SOURCE_BPS) + dur * (OURS_BPS + WAV_BPS)
    free = shutil.disk_usage(w).free
    if need > free:
        raise SystemExit(f"не хватает места на диске: нужно ~{need / 1e9:.1f} ГБ, свободно {free / 1e9:.1f} ГБ")


def download(url: str, dst: Path, tries: int = 3, what: str = "", timeout: int = 60) -> str:
    """One file over HTTP into `dst`: written as `dst.part`, checked against Content-Length, renamed.
    A dropped connection resumes from where it stopped (Range), up to `tries` times; a 4xx is not retried.
    Returns the address after redirects, for `sniff`. `what` names the file in messages."""
    tmp = dst.with_name(dst.name + ".part")
    tmp.unlink(missing_ok=True)
    final = url
    for attempt in range(tries + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {**UA, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                final = r.geturl()
                resumed = have and r.status == 206
                if resumed and not str(r.headers.get("Content-Range") or "").startswith(f"bytes {have}-"):
                    tmp.unlink()  # not the bytes that follow ours: the next try starts from zero
                    raise OSError("пришёл не тот кусок")
                length = r.headers.get("Content-Length")
                total = (have if resumed else 0) + int(length) if length else None
                with tmp.open("ab" if resumed else "wb") as f:
                    while chunk := r.read(1 << 16):
                        f.write(chunk)
            got = tmp.stat().st_size
            if total is not None and got != total:
                raise OSError(f"пришло {got} из {total} байт")
            os.replace(tmp, dst)
            return final
        except (OSError, http.client.HTTPException) as e:  # urllib's errors are OSErrors; a cut body is not
            refused = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code not in (408, 429)
            if attempt == tries or refused:
                raise SystemExit(f"{what or dst.name} не скачался: {e}") from None
            say(f"{what or dst.name}: {e}, докачиваю")
            time.sleep(2)
    raise AssertionError("unreachable")


def fetch_recording(ref: str, w: Path) -> list[str]:
    """The parts of a found recording, as sources for build_audio: YouTube links (yt-dlp downloads them
    with their captions) or mp3 files downloaded here into the work dir, one at a time."""
    kind, parts = recording(ref)
    if not parts:
        raise SystemExit("у озвучки нет частей")
    check_space(parts, w)
    if kind == "yt":
        return [p["url"] for p in parts]
    out = []
    for i, p in enumerate(parts, 1):
        if not str(p.get("url", "")).startswith("https://"):
            raise SystemExit("источник дал ссылку не по https")
        if i > 1:
            time.sleep(0.5)  # one part after another, never in parallel: the site is a library, not a CDN
        say(f"часть {i}/{len(parts)}")
        dst = w / f"part{i:02d}.mp3"
        download(p["url"], dst)
        out.append(str(dst))
    return out


def build_audio(sources: list[str], w: Path, lang: str) -> None:
    """Everything in the work dir: audio.m4a, audio16k.wav and the joined captions, if any."""
    parts = []
    for i, src in enumerate(sources, 1):
        if is_url(src) and len(sources) > 1:
            say(f"часть {i}/{len(sources)}")
        parts.append(fetch_audio(src, w, i, lang))
    offsets, total = [], 0.0
    for p in parts:
        offsets.append(total)
        total += duration_of(p)
    # playable file (AAC) and 16 kHz mono WAV for alignment, both from the concatenation of all parts:
    # the timing is measured on the sources, so it does not depend on the encoder
    lst = w / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    say("склеиваю")
    concat = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vn"]
    run([*concat, *aac_args(), str(w / "audio.m4a")])
    run([*concat, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(w / "audio16k.wav")])
    # captions: shift each part's events by its offset and join into one json3
    events = []
    for p, off in zip(parts, offsets, strict=True):
        caps = sorted(w.glob(f"{p.stem}.*.json3"))
        if not caps:
            continue
        data = json.loads(caps[0].read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            if "tStartMs" in ev:
                ev["tStartMs"] = int(ev["tStartMs"] + off * 1000)
                events.append(ev)
    if events:
        (w / "yt.merged.json3").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    for p in parts:
        p.unlink()  # the concatenation is the source from now on; the space is free before the slow steps
    lst.unlink()


def land_audio(w: Path, d: Path) -> None:
    """The new audio, its timing and its captions replace the old ones in one step each; until here the
    book kept its old audio.m4a, timing.json and book.toml. stamp() follows and comes last."""
    for name in ("audio.m4a", "timing.json"):
        if not (w / name).exists():
            raise SystemExit(f"{name} не собран")
    caps = [*sorted(w.glob("yt.*.json3")), *[f for f in (w / "whisper.json3",) if f.exists()]]
    land(w / "audio.m4a", d / "audio.m4a")
    land(w / "timing.json", d / "timing.json")
    # the old version's captions and other playable copies belong to the old audio
    for old in [*d.glob("yt.*.json3"), d / "whisper.json3", d / "audio.mp3", d / "yt.webm"]:
        old.unlink(missing_ok=True)
    for c in caps:
        land(c, d / c.name)


def land_text(w: Path, d: Path) -> None:
    """The new text and its pictures move into the book once everything else is ready: until here a book
    being given another text kept its old book.json, timing.json and edition, whatever happened. Another text
    is not the old one extracted again: no map of the old sentences (editions.json, see reextract.py) leads to it."""
    (d / "editions.json").unlink(missing_ok=True)
    (d / "images").mkdir(exist_ok=True)
    for f in (w / "images").iterdir() if (w / "images").is_dir() else ():
        land(f, d / "images" / f.name)
    land(w / "book.json", d / "book.json")


FRAGMENT_RE = re.compile(r"конец ознакомительного фрагмента|ознакомительн\w+ фрагмент\w*|купить полную версию", re.I)


def fragment_note(book: dict) -> str:
    """The literal phrase, if the text ends the way sample editions do. Quoted on the card, never judged."""
    tail = " ".join(b.get("text", "") for b in book.get("blocks", [])[-8:])
    m = FRAGMENT_RE.search(tail)
    return m.group(0) if m else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--text", action="append", default=[], help="text source; repeat for volumes")
    ap.add_argument("--audio", action="append", default=[], help="audio source; repeat for parts")
    ap.add_argument("--audio-ref", default="", help="a recording found by the audio search (sources.audio)")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--narrator", default="")
    ap.add_argument("--translator", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--no-align", action="store_true", help="skip the slow MMS pass (caption timing only)")
    ap.add_argument("--whisper-model", default="small")
    args = ap.parse_args()

    # a stop (SIGTERM to the job's process group) unwinds like an error, so the cleanup below runs
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    w = take(work_dir(args.slug))  # downloads and intermediate files, outside the (iCloud) library
    if w is None:  # a re-extraction of this book is running: its work dir is not ours to wipe
        sys.exit(f"{args.slug}: книга уже обрабатывается")
    d = BOOKS / args.slug
    new = not d.exists()
    d.mkdir(parents=True, exist_ok=True)
    try:
        build(args, d, w)
    finally:
        tidy(d)  # finished, failed or stopped: the downloads and derived audio are not kept
        shutil.rmtree(w, ignore_errors=True)
        if new and not any(d.iterdir()):
            d.rmdir()  # a book that never came to be leaves no folder (the server's jobs keep theirs for the log)


def build(args: argparse.Namespace, d: Path, w: Path) -> None:
    if args.text:
        build_text(args.text, d, args.title, args.author, w)
    elif not (d / "book.json").exists():
        raise SystemExit("no text: pass --text, or use an existing book slug to attach audio")
    text = w / "book.json" if (w / "book.json").exists() else d / "book.json"  # a new text waits in w
    book = json.loads(text.read_text(encoding="utf-8"))

    # new text under existing captions (an edition replaced): the word timing is rebuilt from them
    has_captions = any(d.glob("yt.*.json3")) or (d / "whisper.json3").exists()
    has_audio = any((d / name).exists() for name in PLAYABLE)
    new_audio = bool(args.audio or args.audio_ref)
    retime = bool(args.text) and not new_audio and has_captions and has_audio
    if retime:
        run([PY, str(PIPE / "anchors.py"), str(d), "--work", str(w)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(w)])
    if new_audio:
        # the old audio, timing and book.toml stay as they are until everything new is ready in w
        sources = fetch_recording(args.audio_ref, w) if args.audio_ref else args.audio
        build_audio(sources, w, args.lang)
        if not (w / "yt.merged.json3").exists():
            say("распознаю речь: субтитров нет, это долго")
            run([PY, str(PIPE / "transcribe.py"), str(w), "--model", args.whisper_model, "--lang", args.lang])
        say("размечаю")
        run([PY, str(PIPE / "anchors.py"), str(d), "--work", str(w)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(w)])

    toml = d / "book.toml"
    meta = {}
    if toml.exists():
        import tomllib

        meta = tomllib.loads(toml.read_text(encoding="utf-8"))
    meta.setdefault("slug", args.slug)
    meta["title"] = clean_title(args.title or meta.get("title") or book.get("title", args.slug))
    meta["author"] = args.author or meta.get("author") or book.get("author", "")
    meta["language"] = args.lang
    if args.text:
        meta["text_source"] = " | ".join(args.text)
        meta["translator"] = args.translator
        meta["year"] = args.year
        meta["fragment_note"] = fragment_note(book)
    if new_audio:
        meta["audio_source"] = args.audio_ref or " | ".join(args.audio)
        meta["narrator"] = args.narrator or meta.get("narrator", "")
        land_audio(w, d)
    elif retime:
        land(w / "timing.json", d / "timing.json")
    if (w / "book.json").exists():
        land_text(w, d)
    tmp = d / "book.toml.tmp"
    tmp.write_text("".join(f"{k} = {toml_str(v)}\n" for k, v in meta.items() if v != ""), encoding="utf-8")
    if toml.exists():  # the library orders new books by this time: a book given audio or a text is not new
        st = toml.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, toml)
    # a new edition only with new text: it is what makes sentence positions stale; new audio shows in the sizes
    stamp(d, new_edition=bool(args.text))

    print(
        f"\nready: http://127.0.0.1:8765/?book={args.slug}" + ("  (caption timing)" if new_audio else "  (text only)"),
        flush=True,
    )
    if (new_audio or retime) and not args.no_align:
        print("running precise MMS alignment (about 15 min per hour of audio, low priority)...", flush=True)
        run([PY, str(PIPE / "align.py"), str(d), "--work", str(w)])
        print("done: precise timing", flush=True)


if __name__ == "__main__":
    main()
