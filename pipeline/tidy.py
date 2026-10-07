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
import fcntl
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# names every finished book can lose: download parts, the concatenation list, logs of earlier versions,
# the source page of an extracted text, uploads, and the alignment inputs (both rebuilt when needed)
DERIVED = ("parts", "parts.txt", "audio16k.wav", "anchors.json", "align.log", "ffmpeg.log", "book.html")
PLAYABLE = ("audio.m4a", "audio.mp3")
# a *.tmp younger than this may be a write still under way (a rename's book.toml.tmp, a picture being copied)
STALE_TMP = 60.0


def _stale(p: Path) -> bool:
    """Left behind rather than being written: by its inode change time, which a writer that gives the file an
    older modification time (to keep book.toml's) still moves to now."""
    try:
        return time.time() - p.stat().st_ctime > STALE_TMP
    except OSError:
        return False


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
    for p in [*d.glob("*.tmp"), *d.glob("images/*.tmp")]:
        if _stale(p):
            p.unlink(missing_ok=True)
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


@contextmanager
def _claiming(root: Path):
    """One claim at a time among all processes: a check and the claim after it are one step."""
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # and with it the lock


def _claim(w: Path) -> Path:
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    (w / "pid").write_text(str(os.getpid()), encoding="utf-8")
    return w


def claim(w: Path) -> Path:
    """A fresh work dir for this process: what an earlier, dead run left is not trusted."""
    with _claiming(w.parent):
        return _claim(w)


def take(w: Path) -> Path | None:
    """claim(w) unless a live job holds it (then None). The check and the claim are one step among claimers:
    a job claiming the same work dir at the same moment is either seen holding it or claims after this."""
    with _claiming(w.parent):
        return None if held(w) else _claim(w)


def started(pid: int) -> float | None:
    """When a process started (seconds since the epoch, to the second), None when there is no such process."""
    out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", out)
    if not m:
        return None
    days, hours, minutes, seconds = (int(x or 0) for x in m.groups())
    return time.time() - (((days * 24 + hours) * 60 + minutes) * 60 + seconds)


def held(w: Path) -> bool:
    """True while the process that claimed this work dir is alive. A pid the system has since given to
    another process (after a reboot, say) is not the job's: that one started after the claim was written.
    Nor is one this user may not signal: a job runs as the user."""
    p = w / "pid"
    try:
        pid = int(p.read_text(encoding="utf-8"))
        claimed = p.stat().st_mtime
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    start = started(pid)
    return start is not None and start <= claimed + 2  # etime counts whole seconds


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
