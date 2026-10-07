#!/usr/bin/env python3
"""Remove what a book no longer needs. Every stage calls this when it is done, so downloads, parts and
derived audio never outlive their use. Kept: the book itself (book.toml, book.json, timing.json, the
playable audio, images, cover), its reading state, the search result it came from, and the captions:
they are small, and without them a replaced text could not be timed again without downloading the
audio a second time.

  python pipeline/tidy.py <book_dir> [...]
"""

from __future__ import annotations

import errno
import os
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
    for p in [*d.glob("part[0-9][0-9].*"), *d.glob("upload_*"), *d.glob("*.part"), *d.glob("*.tmp")]:
        p.unlink()
        gone.append(p.name)
    # the downloaded original goes once a playable copy exists; it is never the only audio deleted
    if any((d / name).exists() for name in PLAYABLE):
        for p in d.glob("yt.webm"):
            p.unlink()
            gone.append(p.name)
    return gone


def work_root() -> Path:
    """Where jobs build: outside the library, which may be in iCloud Drive and would upload every
    intermediate file. Read on each call so a test (or a run) can point it elsewhere."""
    return Path(os.environ.get("READSYNC_WORK") or Path.home() / "Library" / "Caches" / "readsync" / "jobs")


def work_dir(name: str) -> Path:
    return work_root() / name


def claim(w: Path) -> Path:
    """A fresh work dir for this process: what an earlier, dead run left is not trusted."""
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    (w / "pid").write_text(str(os.getpid()), encoding="utf-8")
    return w


def held(w: Path) -> bool:
    """True while the process that claimed this work dir is alive."""
    try:
        os.kill(int((w / "pid").read_text(encoding="utf-8")), 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def land(src: Path, dst: Path) -> None:
    """Move a finished file into the book in one step. Across volumes it is copied next to its place
    first (a *.tmp, swept by tidy if the copy is cut short) and renamed from there."""
    try:
        os.replace(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        src.unlink()


def main() -> None:
    for arg in sys.argv[1:]:
        d = Path(arg)
        if (d / "book.toml").exists():
            gone = tidy(d)
            if gone:
                print(f"{d.name}: {', '.join(gone)}", flush=True)


if __name__ == "__main__":
    main()
