#!/usr/bin/env python3
"""Add a book to readsync: fetch text and audio, build the sync map, prepare playback audio.

Usage:
  python pipeline/add_book.py <slug> --text <url|book.html|book.fb2|book.fb2.zip> --audio <youtube-url|file> \
      [--title T] [--author A] [--narrator N] [--lang ru] [--no-align] [--whisper-model small]

Text sources: reader.fantasy-worlds.org "read.html" pages, or FB2 files.
Audio sources: a YouTube URL (audio + auto-captions via yt-dlp) or a local audio file.
Without captions the audio is transcribed with faster-whisper for coarse anchoring.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PIPE = ROOT / "pipeline"


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def fetch_text(src: str, d: Path) -> str:
    """Return the extractor kind: 'html' or 'fb2'."""
    if is_url(src):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if data.lstrip().startswith(b"<?xml") or b"<FictionBook" in data[:2000]:
            (d / "book.fb2").write_bytes(data)
            return "fb2"
        (d / "book.html").write_bytes(data)
        return "html"
    p = Path(src).expanduser()
    if p.name.lower().endswith((".fb2", ".fb2.zip")):
        shutil.copy(p, d / ("book.fb2.zip" if p.name.lower().endswith(".zip") else "book.fb2"))
        return "fb2"
    shutil.copy(p, d / "book.html")
    return "html"


def fetch_audio(src: str, d: Path, lang: str) -> Path:
    if is_url(src):
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
                "yt.%(ext)s",
                src,
            ],
            cwd=d,
        )
        return next(f for f in d.iterdir() if f.stem == "yt" and f.suffix not in (".json3", ".part"))
    p = Path(src).expanduser()
    dst = d / ("source" + p.suffix.lower())
    if not dst.exists():
        shutil.copy(p, dst)
    return dst


def prepare_audio(src: Path, d: Path) -> None:
    if not (d / "audio16k.wav").exists():
        run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
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
    if src.suffix.lower() == ".mp3":
        return  # browsers play mp3 directly (serve.py picks audio.mp3)
    if not (d / "audio.m4a").exists():
        codec = (
            ["-c:a", "copy"]
            if src.suffix.lower() in (".m4a", ".m4b", ".mp4", ".aac")
            else ["-c:a", "aac", "-b:a", "96k"]
        )
        run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vn",
                *codec,
                "-movflags",
                "+faststart",
                str(d / "audio.m4a"),
            ]
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--text", required=True)
    ap.add_argument("--audio", default="", help="YouTube URL or local audio file; omit for a text-only book")
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--narrator", default="")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--no-align", action="store_true", help="skip the slow MMS pass (caption timing only)")
    ap.add_argument("--whisper-model", default="small")
    args = ap.parse_args()

    d = ROOT / "books" / args.slug
    d.mkdir(parents=True, exist_ok=True)
    kind = fetch_text(args.text, d)
    run([PY, str(PIPE / ("extract_fb2.py" if kind == "fb2" else "extract_text.py")), str(d)])
    if args.audio:
        audio_src = fetch_audio(args.audio, d, args.lang)
        prepare_audio(audio_src, d)
        if audio_src.suffix.lower() == ".mp3" and not (d / "audio.mp3").exists():
            (d / "audio.mp3").symlink_to(audio_src.name)
        if not list(d.glob("yt.*.json3")) and not (d / "whisper.json3").exists():
            print("no captions: transcribing with faster-whisper (slow)", flush=True)
            run([PY, str(PIPE / "transcribe.py"), str(d), "--model", args.whisper_model, "--lang", args.lang])
        run([PY, str(PIPE / "anchors.py"), str(d)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(d)])

    import json

    book = json.loads((d / "book.json").read_text(encoding="utf-8"))
    toml = d / "book.toml"
    if not toml.exists():
        esc = lambda s: s.replace('"', '\\"')  # noqa: E731
        toml.write_text(
            f'slug = "{args.slug}"\ntitle = "{esc(args.title or book.get("title", args.slug))}"\n'
            f'author = "{esc(args.author or book.get("author", ""))}"\nlanguage = "{args.lang}"\n'
            f'text_source = "{esc(args.text)}"\naudio_source = "{esc(args.audio)}"\n'
            f'narrator = "{esc(args.narrator)}"\n',
            encoding="utf-8",
        )
    print(
        f"\nready: http://127.0.0.1:8765/?book={args.slug}" + ("  (caption timing)" if args.audio else "  (text only)")
    )
    if args.audio and not args.no_align:
        print("running precise MMS alignment (about 7 min per hour of audio)...", flush=True)
        run([PY, str(PIPE / "align.py"), str(d)])
        print("done: precise timing")


if __name__ == "__main__":
    main()
