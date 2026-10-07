"""Re-encoding a finished book's audio: smaller, and the timing still holds. No network; needs ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import compact  # noqa: E402
from manifest import stamp  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")

# tones that change pitch every third of a second, cut by pauses on another rhythm: no lag looks alike
TONE = "sin(2*PI*(220+110*mod(floor(t*3),5))*t)*gt(mod(t,1.3),0.4)+0.01*sin(2*PI*3500*t)"


def toml(d: Path) -> dict:
    return tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))


def test_compact_keeps_time_within_a_millisecond(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    d = tmp_path / "b"
    d.mkdir()
    signal = f"aevalsrc='0.5*({TONE})|0.4*({TONE})':s=48000:d=30"  # 30 s, stereo
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", signal, "-c:a", "aac", "-b:a", "96k"]
        + [str(d / "audio.m4a")],
        check=True,
    )
    original = tmp_path / "original.m4a"
    shutil.copy(d / "audio.m4a", original)
    (d / "timing.json").write_text('{"words": []}', encoding="utf-8")
    (d / "book.toml").write_text('title = "B"\n', encoding="utf-8")
    stamp(d)
    before = toml(d)

    print(compact.compact(d))

    after = toml(d)
    assert after["edition"] == before["edition"] and after["id"] == before["id"]
    size = (d / "audio.m4a").stat().st_size
    assert f"audio.m4a:{size}" in after["files"] and size < original.stat().st_size
    assert compact.probe(d / "audio.m4a", "stream=channels", stream=True) == "1"
    assert abs(compact.duration(d / "audio.m4a") - compact.duration(original)) < 0.1
    for start in compact.windows(compact.duration(original)):
        new, old = compact.decode(d / "audio.m4a", start, 20), compact.decode(original, start, 20)
        assert abs(compact.shift_ms(new, old)) < 1
        # the measure itself sees a shift: 80 samples at 16 kHz are 5 ms
        assert compact.shift_ms(np.concatenate([np.zeros(80, np.float32), new]), old) == pytest.approx(5.0)
    assert not (tmp_path / "work" / "b.compact").exists()
    assert compact.compact(d).startswith("skipped")  # already small: left alone
