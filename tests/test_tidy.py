"""What a finished book keeps and what it loses. No network."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import tidy as tidy_module  # noqa: E402
from tidy import tidy  # noqa: E402


def touch(d: Path, *names: str) -> None:
    for n in names:
        (d / n).parent.mkdir(parents=True, exist_ok=True)
        (d / n).write_text("x", encoding="utf-8")


def test_leftovers_go_and_the_book_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(tidy_module, "STALE_TMP", -1.0)  # every *.tmp here was left long ago
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
        "whisper.json3.tmp",
        "audio.m4a.tmp",
        "images/1.jpg.tmp",
    )
    touch(tmp_path, *keep, *junk)
    tidy(tmp_path)
    assert all((tmp_path / n).exists() for n in keep)
    assert not any((tmp_path / n).exists() for n in junk)


def test_the_only_audio_is_never_deleted(tmp_path):
    touch(tmp_path, "book.toml", "yt.webm")
    tidy(tmp_path)
    assert (tmp_path / "yt.webm").exists()


def test_manifest_keeps_the_id_and_moves_the_edition(tmp_path):
    from manifest import stamp

    touch(tmp_path, "book.json", "timing.json")
    (tmp_path / "book.toml").write_text('title = "T"\n', encoding="utf-8")
    stamp(tmp_path, new_edition=True)
    first = (tmp_path / "book.toml").read_text(encoding="utf-8")
    stamp(tmp_path)
    assert (tmp_path / "book.toml").read_text(encoding="utf-8") == first  # a re-stamp of the same files changes nothing
    stamp(tmp_path, new_edition=True)
    second = (tmp_path / "book.toml").read_text(encoding="utf-8")
    pick = lambda text, key: next(ln for ln in text.splitlines() if ln.startswith(key + " ="))  # noqa: E731
    assert pick(first, "id") == pick(second, "id") and pick(first, "edition") != pick(second, "edition")
    assert pick(second, "files") == 'files = "book.json:1,timing.json:1"'


def test_a_restamp_keeps_the_time_the_library_orders_by(tmp_path):
    """New audio or an alignment re-stamps a book; it is not a new book for that."""
    from manifest import stamp

    touch(tmp_path, "book.json")
    (tmp_path / "book.toml").write_text('title = "T"\n', encoding="utf-8")
    os.utime(tmp_path / "book.toml", (1_000_000_000, 1_000_000_000))
    stamp(tmp_path, new_edition=True)
    assert (tmp_path / "book.toml").stat().st_mtime == 1_000_000_000


def test_a_given_edition_lands_with_the_sizes_in_one_write(tmp_path):
    """A re-extraction's edition and the sizes of its files: another device never sees one without the other."""
    import tomllib

    from manifest import stamp

    touch(tmp_path, "book.json")
    (tmp_path / "book.toml").write_text('title = "T"\nedition = "old"\n', encoding="utf-8")
    stamp(tmp_path, edition="e2")
    meta = tomllib.loads((tmp_path / "book.toml").read_text(encoding="utf-8"))
    assert meta["edition"] == "e2" and meta["files"].startswith("book.json:")


def test_toml_strings_survive_line_breaks_and_control_characters():
    import tomllib

    from manifest import toml_str

    title = 'Война и мир.\n Том 1\t"цитата" \\ \x00\x1b\x7f'
    assert tomllib.loads(f"title = {toml_str(title)}\n")["title"] == title


def test_a_pid_given_to_another_process_holds_nothing(tmp_path):
    """A job's work dir outlives a power cut; after the reboot its pid may belong to a daemon (which this user
    may not signal) or to any process started since. Neither is the job."""
    import subprocess

    from tidy import claim, held

    w = claim(tmp_path / "w")
    assert held(w)  # this process claimed it
    (w / "pid").write_text("1", encoding="utf-8")  # launchd
    assert not held(w)
    other = subprocess.Popen(["sleep", "30"])
    try:
        (w / "pid").write_text(str(other.pid), encoding="utf-8")
        os.utime(w / "pid", (1_000_000_000, 1_000_000_000))  # written long before that process started
        assert not held(w)
    finally:
        other.kill()
        other.wait()


def test_a_tmp_being_written_stays(tmp_path, monkeypatch):
    """A rename writes book.toml.tmp with book.toml's old time and then moves it into place: a tidy at that
    moment (another stage ending) must not take it away. Left behind for a minute, it goes, pictures' too."""
    touch(tmp_path, "book.toml", "book.toml.tmp", ".book.toml.tmp", "images/a.png.tmp")
    for n in ("book.toml.tmp", ".book.toml.tmp", "images/a.png.tmp"):
        os.utime(tmp_path / n, (1_000_000_000, 1_000_000_000))
    assert tidy(tmp_path) == []
    assert (tmp_path / "book.toml.tmp").exists() and (tmp_path / "images" / "a.png.tmp").exists()
    later = tidy_module.time.time() + 120
    monkeypatch.setattr(tidy_module.time, "time", lambda: later)
    assert sorted(tidy(tmp_path)) == [".book.toml.tmp", "a.png.tmp", "book.toml.tmp"]
    assert (tmp_path / "book.toml").exists()


def test_a_cross_volume_copy_cut_short_is_swept(tmp_path, monkeypatch):
    """land() copies a picture next to its place as images/<name>.tmp across volumes; cut short, the copy
    is swept once it is stale."""
    import errno
    import shutil

    d, w = tmp_path / "bk", tmp_path / "w"
    (d / "images").mkdir(parents=True)
    w.mkdir()
    (w / "a.png").write_bytes(b"x" * 10)
    real_replace = os.replace

    def exdev(src, dst):
        if str(src).startswith(str(w)):
            raise OSError(errno.EXDEV, "cross-device")
        return real_replace(src, dst)

    def cut(src, dst):
        Path(dst).write_bytes(b"x")
        raise KeyboardInterrupt("copy cut short")

    with monkeypatch.context() as m:
        m.setattr(tidy_module.os, "replace", exdev)
        m.setattr(tidy_module.shutil, "copyfile", cut)
        with pytest.raises(KeyboardInterrupt):
            tidy_module.land(w / "a.png", d / "images" / "a.png")
    assert shutil.copyfile is not cut and (d / "images" / "a.png.tmp").exists()
    monkeypatch.setattr(tidy_module, "STALE_TMP", -1.0)
    assert tidy(d) == ["a.png.tmp"]
    assert list((d / "images").iterdir()) == []


def test_take_refuses_a_work_dir_a_live_job_holds(tmp_path):
    """reextract's check and claim are one step: a work dir a live job holds is not taken (nor swept away),
    one a dead run left is taken fresh."""
    from tidy import claim, held, take

    w = claim(tmp_path / "w")
    (w / "mine").write_text("x", encoding="utf-8")
    assert take(w) is None and (w / "mine").exists()
    (w / "pid").write_text("1", encoding="utf-8")  # not this user's job
    assert take(w) == w and held(w) and not (w / "mine").exists()
