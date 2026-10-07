"""Caption anchors and the timing built from them. No network; the last test needs ffmpeg."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import anchors  # noqa: E402
from timing_from_anchors import interpolate  # noqa: E402

WORDS = " ".join(f"слово{i}" for i in range(20))
BOOK = {"blocks": [{"kind": "p", "text": WORDS, "audio": True}]}
# captions for the first ten words only, one a second: the recording goes on without them
CAPTIONS = {"events": [{"tStartMs": i * 1000, "segs": [{"utf8": f"слово{i}"}]} for i in range(10)]}


def test_words_past_the_last_caption_keep_their_own_time():
    """A part without captions (or closing music) is still audio: the duration is the recording's, and the
    words after the last caption are spread over it, not stacked on one instant."""
    res = anchors.build(BOOK, CAPTIONS, 20.0)
    assert res["duration"] == 20.0
    tail = [w[3] for w in interpolate(res["words"], res["anchors"], res["duration"])[10:]]
    assert len(set(tail)) == 10 and tail == sorted(tail) and tail[-1] <= 20.0
    assert anchors.build(BOOK, CAPTIONS)["duration"] == 9.5  # no recording to measure: the captions' end


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_anchors_measure_the_recording(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (tmp_path / "book.json").write_text(json.dumps(BOOK, ensure_ascii=False), encoding="utf-8")
    (w / "yt.merged.json3").write_text(json.dumps(CAPTIONS, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=15",
            str(w / "audio.m4a"),
        ],
        check=True,
    )
    subprocess.run([sys.executable, anchors.__file__, str(tmp_path), "--work", str(w)], check=True, capture_output=True)
    assert json.loads((w / "anchors.json").read_text(encoding="utf-8"))["duration"] == pytest.approx(15, abs=0.2)
