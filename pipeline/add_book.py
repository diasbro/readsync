#!/usr/bin/env python3
"""Add a book to readsync, or add audio to an existing one.

Usage:
  python pipeline/add_book.py <slug> --text <url|file> [--text <url|file> ...] [--audio <url|file> ...]
      [--title T] [--author A] [--narrator N] [--lang ru] [--no-align] [--whisper-model small]
  python pipeline/add_book.py <slug> --audio <url|file> [...]      # attach audio to an existing text-only book

Text sources: fantasy-worlds reader pages, any HTML page, FB2 / FB2.zip, EPUB, TXT (local files or
direct download links). Several --text values are volumes of one book and are merged in order.
Audio sources: YouTube URLs or local files; several --audio values are parts and are joined in order.
Without captions the audio is transcribed with faster-whisper for coarse anchoring.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# the same root the server uses: the Mac app keeps the books outside the code it updates
BOOKS = Path(os.environ.get("READSYNC_BOOKS") or ROOT / "books").expanduser()
PY = sys.executable
PIPE = ROOT / "pipeline"
UA = {"User-Agent": "Mozilla/5.0"}


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


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
    """Download or copy one text source into part_dir; return the extractor kind."""
    part_dir.mkdir(parents=True, exist_ok=True)
    if is_url(src):
        req = urllib.request.Request(src, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            final = r.geturl()
        kind = sniff(data, final)
    else:
        p = Path(src).expanduser()
        data = p.read_bytes()
        kind = sniff(data, p.name)
        final = p.name
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
        try:
            r = urllib.request.Request(f"{base}/images/{n}", headers=UA)
            (d / "images" / n).write_bytes(urllib.request.urlopen(r, timeout=60).read())
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


def build_text(sources: list[str], d: Path, title: str, author: str) -> None:
    parts_dir = d / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    for i, src in enumerate(sources, 1):
        part = parts_dir / f"{i:02d}"
        kind = fetch_text(src, part)
        run([PY, str(PIPE / EXTRACTORS[kind]), str(part)])
        check_real_book(json.loads((part / "book.json").read_text(encoding="utf-8")), len(sources))
    # merge parts (a single part is copied through), then gather images into the book's images/
    cmd = [PY, str(PIPE / "merge_books.py"), str(d), "--title", title, "--author", author]
    run(cmd)
    (d / "images").mkdir(exist_ok=True)
    for part in sorted(parts_dir.iterdir()):
        if (part / "images").is_dir():
            for f in (part / "images").iterdir():
                shutil.copy(f, d / "images" / f.name)


def fetch_audio(src: str, d: Path, idx: int, lang: str) -> Path:
    """Download one audio part (YouTube via yt-dlp, with auto captions) or copy a local file."""
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
            cwd=d,
        )
        return next(f for f in d.iterdir() if f.stem == f"part{idx:02d}" and f.suffix not in (".json3", ".part"))
    p = Path(src).expanduser()
    dst = d / f"part{idx:02d}{p.suffix.lower()}"
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


def build_audio(sources: list[str], d: Path, lang: str) -> None:
    parts = [fetch_audio(src, d, i, lang) for i, src in enumerate(sources, 1)]
    offsets, total = [], 0.0
    for p in parts:
        offsets.append(total)
        total += duration_of(p)
    # playable file (AAC) and 16 kHz mono WAV for alignment, both from the concatenation of all parts
    lst = d / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(d / "audio.m4a"),
        ]
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(d / "audio.m4a"),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(d / "audio16k.wav"),
        ]
    )
    # captions: shift each part's events by its offset and join into one json3
    events = []
    for p, off in zip(parts, offsets, strict=True):
        caps = sorted(d.glob(f"{p.stem}.*.json3"))
        if not caps:
            continue
        data = json.loads(caps[0].read_text(encoding="utf-8"))
        for ev in data.get("events", []):
            if "tStartMs" in ev:
                ev["tStartMs"] = int(ev["tStartMs"] + off * 1000)
                events.append(ev)
    for old in d.glob("yt.*.json3"):
        old.unlink()
    if events:
        (d / "yt.merged.json3").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    elif (d / "whisper.json3").exists():
        (d / "whisper.json3").unlink()
    for p in parts:
        if len(parts) > 1 or p.suffix != ".webm":
            p.unlink()  # the concatenated m4a is the source from now on
    lst.unlink()


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
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--narrator", default="")
    ap.add_argument("--translator", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--no-align", action="store_true", help="skip the slow MMS pass (caption timing only)")
    ap.add_argument("--whisper-model", default="small")
    args = ap.parse_args()

    d = BOOKS / args.slug
    d.mkdir(parents=True, exist_ok=True)
    if args.text:
        build_text(args.text, d, args.title, args.author)
    elif not (d / "book.json").exists():
        raise SystemExit("no text: pass --text, or use an existing book slug to attach audio")
    book = json.loads((d / "book.json").read_text(encoding="utf-8"))

    # new text under existing captions (an edition replaced): the word timing is rebuilt from them
    retime = bool(args.text) and not args.audio and (d / "yt.merged.json3").exists() and (d / "audio16k.wav").exists()
    if retime:
        for stale in ("timing.json", "anchors.json", "align.log"):
            (d / stale).unlink(missing_ok=True)
        run([PY, str(PIPE / "anchors.py"), str(d)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(d)])
    if args.audio:
        for stale in ("timing.json", "anchors.json", "align.log"):
            (d / stale).unlink(missing_ok=True)
        build_audio(args.audio, d, args.lang)
        if not (d / "yt.merged.json3").exists() and not (d / "whisper.json3").exists():
            print("no captions: transcribing with faster-whisper (slow)", flush=True)
            run([PY, str(PIPE / "transcribe.py"), str(d), "--model", args.whisper_model, "--lang", args.lang])
        run([PY, str(PIPE / "anchors.py"), str(d)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(d)])

    toml = d / "book.toml"
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    meta = {}
    if toml.exists():
        import tomllib

        meta = tomllib.loads(toml.read_text(encoding="utf-8"))
    meta.setdefault("slug", args.slug)
    meta["title"] = args.title or meta.get("title") or book.get("title", args.slug)
    meta["author"] = args.author or meta.get("author") or book.get("author", "")
    meta["language"] = args.lang
    if args.text:
        meta["text_source"] = " | ".join(args.text)
        meta["translator"] = args.translator
        meta["year"] = args.year
        meta["fragment_note"] = fragment_note(book)
    if args.audio:
        meta["audio_source"] = " | ".join(args.audio)
        meta["narrator"] = args.narrator or meta.get("narrator", "")
    toml.write_text("".join(f'{k} = "{esc(v)}"\n' for k, v in meta.items() if v != ""), encoding="utf-8")

    print(
        f"\nready: http://127.0.0.1:8765/?book={args.slug}" + ("  (caption timing)" if args.audio else "  (text only)"),
        flush=True,
    )
    if (args.audio or retime) and not args.no_align:
        print("running precise MMS alignment (about 15 min per hour of audio, low priority)...", flush=True)
        run([PY, str(PIPE / "align.py"), str(d)])
        print("done: precise timing", flush=True)


if __name__ == "__main__":
    main()
