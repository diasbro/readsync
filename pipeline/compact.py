#!/usr/bin/env python3
"""Re-encode a finished book's audio.m4a to AAC-LC mono 48 kbit/s, about half the size.

The new file is built in the work dir, outside the library, and replaces the old one only after two
checks: the durations differ by less than MAX_DURATION_DIFF, and three 20 s windows (start, middle,
end) decoded to 16 kHz mono line up with the old file within MAX_SHIFT_MS. Then book.toml is
re-stamped with the same edition: timing.json stays valid, the phone sees the new size and copies
the audio again. On any failure the original is not touched. Books already at or below
SKIP_KBPS are left as they are.

  python pipeline/compact.py <book_dir> [...]
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import stamp  # noqa: E402
from tidy import claim, land, work_dir  # noqa: E402

SKIP_KBPS = 64
MAX_DURATION_DIFF = 0.1  # seconds
MAX_SHIFT_MS = 1.0
WINDOW_SEC = 20.0
SR = 16000
MAX_LAG_SEC = 0.5  # the search range of the cross-correlation
MIN_SIMILARITY = 0.5  # normalized peak: below it the two windows are not the same audio


def aac_args() -> list[str]:
    """The profile every book's audio is encoded with: AAC-LC mono 48 kbit/s. Its encoder delay is
    covered by the edit list, so the decoded audio starts where the source did and the timing holds.
    AudioToolbox's encoder where ffmpeg has it, ffmpeg's own otherwise."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    enc = "aac_at" if re.search(r"(?m)^\s*\S+\s+aac_at\s", out) else "aac"
    return ["-ac", "1", "-c:a", enc, "-b:a", "48k", "-movflags", "+faststart"]


def probe(path: Path, entry: str, stream: bool = False) -> str:
    cmd = ["ffprobe", "-v", "error"] + (["-select_streams", "a:0"] if stream else [])
    cmd += ["-show_entries", entry, "-of", "csv=p=0", str(path)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def duration(path: Path) -> float:
    return float(probe(path, "format=duration"))


def kbps(path: Path) -> float:
    """The audio stream's bit rate; the file's average when the stream does not say."""
    rate = probe(path, "stream=bit_rate", stream=True)
    if rate.isdigit():
        return int(rate) / 1000
    return path.stat().st_size * 8 / duration(path) / 1000


def decode(path: Path, start: float, length: float):
    """`length` seconds from `start` as 16 kHz mono float32 samples."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path)]
        + ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def shift_ms(new, old) -> float | None:
    """How much later `new` sounds than `old`, in ms, by cross-correlation; None when the windows are
    silent or do not hold the same audio."""
    import numpy as np

    n = min(len(new), len(old))
    a = new[:n].astype(np.float64) - new[:n].mean()
    b = old[:n].astype(np.float64) - old[:n].mean()
    energy = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if n == 0 or energy < 1e-9:
        return None
    size = 1 << (2 * n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    lag = min(int(MAX_LAG_SEC * SR), n - 1)
    lags = np.concatenate([np.arange(0, lag + 1), np.arange(-lag, 0)])
    vals = np.concatenate([corr[: lag + 1], corr[size - lag :]])
    k = int(np.argmax(vals))
    if vals[k] / energy < MIN_SIMILARITY:
        return None
    return float(lags[k]) * 1000 / SR


def windows(total: float) -> list[float]:
    length = min(WINDOW_SEC, total)
    last = max(0.0, total - length)
    return sorted({0.0, round(last / 2, 3), round(last, 3)})


def check(new: Path, old: Path) -> str:
    """Empty when `new` can stand in for `old`, else what is wrong."""
    dn, do = duration(new), duration(old)
    if abs(dn - do) >= MAX_DURATION_DIFF:
        return f"duration {dn:.3f} s vs {do:.3f} s"
    length = min(WINDOW_SEC, do)
    shifts = []
    for start in windows(do):
        s = shift_ms(decode(new, start, length), decode(old, start, length))
        if s is not None:
            shifts.append(s)
            if abs(s) >= MAX_SHIFT_MS:
                return f"shifted by {s:+.2f} ms at {start:.0f} s"
    if not shifts:
        return "no window to compare: silent or different audio"
    return ""


def compact(d: Path) -> str:
    """Re-encode d/audio.m4a if it pays; return what happened. Raises SystemExit on a failed check."""
    src = d / "audio.m4a"
    if not src.exists():
        return "no audio.m4a"
    rate = kbps(src)
    if rate <= SKIP_KBPS:
        return f"skipped: already {rate:.0f} kbit/s"
    w = claim(work_dir(d.name + ".compact"))
    try:
        out = w / "audio.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", *aac_args(), str(out)],
            check=True,
        )
        problem = check(out, src)
        if problem:
            raise SystemExit(f"{d.name}: not replaced, {problem}")
        before, after = src.stat().st_size, out.stat().st_size
        land(out, src)
        stamp(d)  # same edition: the text and its timing did not change, only the audio's size
        return f"{rate:.0f} kbit/s, {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB"
    finally:
        shutil.rmtree(w, ignore_errors=True)


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so the work dir goes on a stop too
    failed = False
    for arg in sys.argv[1:]:
        d = Path(arg)
        try:
            print(f"{d.name}: {compact(d)}", flush=True)
        except SystemExit as e:
            if not isinstance(e.code, str):  # a stop, not a failed check
                raise
            print(e.code, file=sys.stderr, flush=True)
            failed = True
        except (subprocess.CalledProcessError, ValueError) as e:
            print(f"{d.name}: {e}", file=sys.stderr, flush=True)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
