#!/usr/bin/env python3
"""Stamp a finished book's identity into book.toml: `id` never changes, `edition` changes whenever the
text is replaced, and `files` lists the size of each file the reader needs. A copy of the book that
arrives piece by piece (iCloud syncs file by file) is complete when the sizes match, new audio shows
in those sizes, and a sentence position saved against another edition is known to be stale.

It also stamps where the main text ends (`text_end`, a sentence index, and `audio_end`, seconds, see
text_end.py): numbers only, outside `files` and `edition`, so adding them never makes a copy look new.

  python pipeline/manifest.py <book_dir> [--new-edition]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_end import audio_end, text_end  # noqa: E402

PARTS = ("book.json", "timing.json", "audio.m4a", "audio.mp3")
ID_RE = re.compile(r'(?m)^id\s*=\s*"[0-9a-f]{32}"')  # a book.toml line holding a valid id


def _set(text: str, key: str, value: str, raw: bool = False) -> str:
    line = f"{key} = {value}" if raw else f'{key} = "{value}"'
    new, n = re.subn(rf"(?m)^{key}\s*=.*$", lambda _: line, text, count=1)
    return new if n else text.rstrip("\n") + "\n" + line + "\n"


def _ends(d: Path, text: str) -> str:
    """book.toml text with `text_end` and `audio_end` for the book as it is now; no timing, no audio_end.
    Files that do not read as a book leave the text as it was: the manifest itself must not fail on them."""
    try:
        book = json.loads((d / "book.json").read_text(encoding="utf-8"))
        timing = json.loads((d / "timing.json").read_text(encoding="utf-8")) if (d / "timing.json").exists() else None
        end, heard = text_end(book), audio_end(book, timing) if timing is not None else None
    except (OSError, ValueError, TypeError, AttributeError, IndexError):
        return text
    text = _set(text, "text_end", str(end), raw=True)
    if heard is not None:
        return _set(text, "audio_end", f"{heard:.2f}", raw=True)
    return re.sub(r"(?m)^audio_end\s*=.*\n?", "", text)


def stamp_ends(d: Path) -> None:
    """Only the end of the main text, for a book stamped before it was: nothing else in book.toml changes,
    its time included (the library orders new books by it)."""
    toml = d / "book.toml"
    st = toml.stat()
    tmp = d / ".book.toml.tmp"
    tmp.write_text(_ends(d, toml.read_text(encoding="utf-8")), encoding="utf-8")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, toml)


def stamp(d: Path, new_edition: bool = False) -> None:
    toml = d / "book.toml"
    text = toml.read_text(encoding="utf-8") if toml.exists() else ""
    if not ID_RE.search(text):
        text = _set(text, "id", uuid.uuid4().hex)
    if new_edition or not re.search(r"(?m)^edition\s*=", text):
        text = _set(text, "edition", uuid.uuid4().hex)
    files = ",".join(f"{n}:{(d / n).stat().st_size}" for n in PARTS if (d / n).exists())
    text = _set(text, "files", files)
    if (d / "book.json").exists():
        text = _ends(d, text)
    tmp = d / ".book.toml.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, toml)  # last: once it lands, the files it lists are all in place


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--new-edition", action="store_true")
    args = ap.parse_args()
    stamp(args.book_dir, args.new_edition)


if __name__ == "__main__":
    main()
