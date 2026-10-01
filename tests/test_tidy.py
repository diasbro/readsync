"""What a finished book keeps and what it loses. No network."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from tidy import tidy  # noqa: E402


def touch(d: Path, *names: str) -> None:
    for n in names:
        (d / n).parent.mkdir(parents=True, exist_ok=True)
        (d / n).write_text("x", encoding="utf-8")


def test_leftovers_go_and_the_book_stays(tmp_path):
    keep = (
        "book.toml",
        "book.json",
        "timing.json",
        "audio.m4a",
        "images/1.jpg",
        "state.json",
        "hits.json",
        "yt.merged.json3",
        "whisper.json3",
    )
    junk = (
        "yt.webm",
        "audio16k.wav",
        "anchors.json",
        "parts/01/book.json",
        "parts.txt",
        "part01.webm",
        "upload_book.fb2",
        "book.html",
        "align.log",
        "ffmpeg.log",
    )
    touch(tmp_path, *keep, *junk)
    tidy(tmp_path)
    assert all((tmp_path / n).exists() for n in keep)
    assert not any((tmp_path / n).exists() for n in junk)


def test_the_only_audio_is_never_deleted(tmp_path):
    touch(tmp_path, "book.toml", "yt.webm")
    tidy(tmp_path)
    assert (tmp_path / "yt.webm").exists()
