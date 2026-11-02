"""Packages a stage needs that the Mac app's Python does not carry: the speech recognizer and the aligner
(hundreds of MB each, and only for audio without captions or for a precise alignment). They are installed
with pip on first use into the app's own folder for such packages, which the app also puts on the server's
path (`libsDir` in app/Readsync.swift). A checkout's .venv has them already and never gets here."""

from __future__ import annotations

import fcntl
import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

LIBS = Path.home() / "Library" / "Application Support" / "readsync" / "pylibs"


def missing(modules: tuple[str, ...]) -> list[str]:
    return [m for m in modules if importlib.util.find_spec(m) is None]


def need(modules: tuple[str, ...], packages: list[str], what: str) -> None:
    """Make `modules` importable: from this Python, from LIBS, or by installing `packages` into LIBS once.
    Two jobs that need them at the same time install them once: the second waits for the first."""
    if str(LIBS) not in sys.path:
        sys.path.append(str(LIBS))  # what this Python has itself comes first
    if not missing(modules):
        return
    LIBS.mkdir(parents=True, exist_ok=True)
    with open(LIBS / ".install.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        importlib.invalidate_caches()
        if not missing(modules):
            return
        print(f"ставлю {what}: один раз, несколько минут", file=sys.stderr, flush=True)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(LIBS)]
        if subprocess.run([*cmd, *packages]).returncode != 0:
            sys.exit(f"не вышло поставить {what}: нужен интернет")
        importlib.invalidate_caches()
    if missing(modules):
        sys.exit(f"не вышло поставить {what}: {', '.join(missing(modules))} не находится")
