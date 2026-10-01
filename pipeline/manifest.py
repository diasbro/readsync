#!/usr/bin/env python3
"""Stamp a finished book's identity into book.toml: `id` never changes, `edition` changes whenever the
text or the audio is replaced, and `files` lists the size of each file the reader needs. A copy of
the book that arrives piece by piece (iCloud syncs file by file) is complete when the sizes match,
and a reading position saved against another edition is known to be stale.

  python pipeline/manifest.py <book_dir> [--new-edition]
"""

from __future__ import annotations

import argparse
import os
import re
import uuid
from pathlib import Path

PARTS = ("book.json", "timing.json", "audio.m4a", "audio.mp3")


def _set(text: str, key: str, value: str) -> str:
    line = f'{key} = "{value}"'
    new, n = re.subn(rf"(?m)^{key}\s*=.*$", lambda _: line, text, count=1)
    return new if n else text.rstrip("\n") + "\n" + line + "\n"


def stamp(d: Path, new_edition: bool = False) -> None:
    toml = d / "book.toml"
    text = toml.read_text(encoding="utf-8") if toml.exists() else ""
    if not re.search(r'(?m)^id\s*=\s*"[0-9a-f]{32}"', text):
        text = _set(text, "id", uuid.uuid4().hex)
    if new_edition or not re.search(r"(?m)^edition\s*=", text):
        text = _set(text, "edition", uuid.uuid4().hex)
    files = ",".join(f"{n}:{(d / n).stat().st_size}" for n in PARTS if (d / n).exists())
    text = _set(text, "files", files)
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
