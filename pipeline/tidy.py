#!/usr/bin/env python3
"""Remove what a book no longer needs. Every stage calls this when it is done, so downloads, parts and
derived audio never outlive their use. Kept: the book itself (book.toml, book.json, timing.json, the
playable audio, images, cover), its reading state, the search result it came from, and the captions:
they are small, and without them a replaced text could not be timed again without downloading the
audio a second time.

  python pipeline/tidy.py <book_dir> [...]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# names every finished book can lose: download parts, the concatenation list, logs of earlier versions,
# the source page of an extracted text, uploads, and the alignment inputs (both rebuilt when needed)
DERIVED = ("parts", "parts.txt", "audio16k.wav", "anchors.json", "align.log", "ffmpeg.log", "book.html")
PLAYABLE = ("audio.m4a", "audio.mp3")


def tidy(d: Path) -> list[str]:
    """Delete the leftovers in one book directory and return their names."""
    gone = []
    for name in DERIVED:
        p = d / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
        else:
            continue
        gone.append(name)
    for p in [*d.glob("part[0-9][0-9].*"), *d.glob("upload_*"), *d.glob("*.part")]:
        p.unlink()
        gone.append(p.name)
    # the downloaded original goes once a playable copy exists; it is never the only audio deleted
    if any((d / name).exists() for name in PLAYABLE):
        for p in d.glob("yt.webm"):
            p.unlink()
            gone.append(p.name)
    return gone


def main() -> None:
    for arg in sys.argv[1:]:
        d = Path(arg)
        if (d / "book.toml").exists():
            gone = tidy(d)
            if gone:
                print(f"{d.name}: {', '.join(gone)}", flush=True)


if __name__ == "__main__":
    main()
