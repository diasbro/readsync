"""What add_book stamps into book.toml when it finishes. No network: every stage is stubbed."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import add_book  # noqa: E402
import tidy  # noqa: E402

PIPE = Path(__file__).resolve().parent.parent / "pipeline"


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch) -> Path:
    """Jobs build in a work dir: here under tmp_path, never in the real ~/Library/Caches."""
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    return tmp_path / "work"


def edition(d: Path) -> str:
    return tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))["edition"]


def finished_book(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)

    def run(cmd, **kw):  # the timing step is the one whose output the book takes in
        if str(cmd[1]).endswith("timing_from_anchors.py"):
            (Path(cmd[2]) / "timing.json").write_text('{"words": []}', encoding="utf-8")

    monkeypatch.setattr(add_book, "run", run)
    monkeypatch.setattr(add_book, "tidy", lambda d: None)
    monkeypatch.setattr(add_book, "build_audio", lambda src, w, lang: (w / "audio.m4a").write_bytes(b"a" * 10))
    monkeypatch.setattr(
        add_book, "build_text", lambda src, d, t, a: (d / "book.json").write_text(json.dumps({"blocks": []}))
    )
    d = tmp_path / "b"
    d.mkdir()
    (d / "book.json").write_text(json.dumps({"title": "B", "blocks": []}), encoding="utf-8")
    (d / "book.toml").write_text(f'title = "B"\nid = "{"a" * 32}"\nedition = "e1"\n', encoding="utf-8")
    return d


def test_new_audio_keeps_the_edition(tmp_path, monkeypatch):
    """Sentence positions stay valid under new audio; the phone sees the new audio in the file sizes."""
    d = finished_book(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--audio", "x.m4a", "--no-align"])
    add_book.main()
    assert edition(d) == "e1"
    assert "audio.m4a:10" in (d / "book.toml").read_text(encoding="utf-8")


def test_new_text_makes_a_new_edition(tmp_path, monkeypatch):
    d = finished_book(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--text", "x.txt", "--no-align"])
    add_book.main()
    assert edition(d) != "e1"


def test_a_failed_text_leaves_no_downloads(tmp_path, monkeypatch):
    """A site that serves a stub instead of the book stops the run early: its downloads go anyway."""
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)

    def stub(src, d, t, a):
        (d / "parts").mkdir()
        (d / "parts" / "1.fb2").write_text("Книга заблокирована.", encoding="utf-8")
        raise SystemExit("на сайте вместо книги заглушка")

    monkeypatch.setattr(add_book, "build_text", stub)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--text", "https://example.org/b"])
    with pytest.raises(SystemExit):
        add_book.main()
    assert not (tmp_path / "b" / "parts").exists()


def test_failed_audio_keeps_old_audio_and_timing(tmp_path, monkeypatch, work_root):
    """New audio is built in the work dir: a failure halfway leaves the book exactly as it was."""
    d = finished_book(tmp_path, monkeypatch)
    monkeypatch.setattr(add_book, "tidy", tidy.tidy)
    (d / "audio.m4a").write_bytes(b"old audio")
    (d / "timing.json").write_text('{"words": [[0, 0, 1, 0.0, 0.5]]}', encoding="utf-8")
    before = {n: (d / n).read_bytes() for n in ("audio.m4a", "timing.json", "book.toml")}

    def broken(src, w, lang):
        (w / "part01.webm").write_bytes(b"part")
        (w / "audio.m4a").write_bytes(b"half an encode")
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(add_book, "build_audio", broken)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--audio", "https://example.org/a", "--no-align"])
    with pytest.raises(subprocess.CalledProcessError):
        add_book.main()
    assert {n: (d / n).read_bytes() for n in before} == before
    assert not (work_root / "b").exists()


def test_sigterm_runs_tidy(tmp_path, work_root):
    """A stop is a SIGTERM: the job unwinds through its cleanup, so no part outlives it."""
    books = tmp_path / "books"
    d = books / "b"
    d.mkdir(parents=True)
    (d / "book.json").write_text(json.dumps({"title": "B", "blocks": []}), encoding="utf-8")
    (d / "upload_book.fb2").write_text("x", encoding="utf-8")
    src = tmp_path / "source.m4a"
    src.write_bytes(b"audio")
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(PIPE)!r})
        import add_book

        def stuck(cmd, **kw):  # the first ffmpeg run: the part is in the work dir by now
            print("encoding", flush=True)
            time.sleep(60)

        add_book.run = stuck
        add_book.duration_of = lambda p: 1.0
        add_book.aac_args = lambda: []
        sys.argv = ["add_book.py", "b", "--audio", {str(src)!r}, "--no-align"]
        add_book.main()
    """)
    env = {**os.environ, "READSYNC_BOOKS": str(books), "READSYNC_WORK": str(work_root)}
    proc = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "encoding"
        assert (work_root / "b" / "part01.m4a").exists()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 143
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert not (work_root / "b").exists()
    assert not [*d.glob("part*"), *d.glob("upload_*")]
    assert (d / "book.json").exists()
