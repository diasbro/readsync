"""Books on disk: what a rename keeps and what it lets go. No network."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path

import pytest

import library


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch) -> Path:
    """Jobs build in a work dir: here under tmp_path, never in the real ~/Library/Caches."""
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    return tmp_path / "work"


def gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_rename_keeps_the_editions_but_forgets_the_query(tmp_path, monkeypatch):
    """The search field must offer the new name, while the editions it was picked from stay a click away."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    book = tmp_path / "kgbt"
    book.mkdir()
    (book / "book.toml").write_text('title = "KGBT"\nauthor = "Пелевин"\n', encoding="utf-8")
    edition = {"title": "KGBT+", "parts": [{"url": "https://example.org/b/1/fb2", "kind": "fb2"}]}
    (book / "hits.json").write_text(json.dumps({"hits": [edition], "query": "старое название"}), encoding="utf-8")

    library.rename_book("kgbt", "KGBT плюс")

    saved = json.loads((book / "hits.json").read_text(encoding="utf-8"))
    assert saved["query"] == ""
    assert saved["hits"] == [edition]
    assert 'title = "KGBT плюс"' in (book / "book.toml").read_text(encoding="utf-8")


def test_rename_without_a_saved_search_touches_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    book = tmp_path / "tiny"
    book.mkdir()
    (book / "book.toml").write_text('title = "Крохотная"\n', encoding="utf-8")

    library.rename_book("tiny", "Совсем крохотная")

    assert not (book / "hits.json").exists()


def test_manifests_skip_books_with_a_job_log_and_survive_a_bad_book(tmp_path, monkeypatch):
    """add.log stays while a job runs or after it failed: such a book is the job's, not the manifest's."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)

    def make(slug: str, toml: str = 'title = "T"\n', text: bool = True) -> Path:
        d = tmp_path / slug
        d.mkdir()
        (d / "book.toml").write_text(toml, encoding="utf-8")
        if text:
            (d / "book.json").write_text("{}", encoding="utf-8")
        return d

    loading = make("loading")
    (loading / "add.log").write_text("downloading", encoding="utf-8")
    stub = make("stub", text=False)
    bad_id = make("bad-id", 'title = "T"\nid = "not-an-id"\n')
    (tmp_path / "broken" / "book.toml").mkdir(parents=True)  # unreadable: must not stop the others
    (tmp_path / "broken" / "book.json").write_text("{}", encoding="utf-8")
    plain = make("plain")

    library.ensure_manifests()

    assert "id =" not in (loading / "book.toml").read_text(encoding="utf-8")
    assert "id =" not in (stub / "book.toml").read_text(encoding="utf-8")
    assert re.search(r'(?m)^id = "[0-9a-f]{32}"$', (bad_id / "book.toml").read_text(encoding="utf-8"))
    assert re.search(r'(?m)^id = "[0-9a-f]{32}"$', (plain / "book.toml").read_text(encoding="utf-8"))


def test_stop_kills_the_whole_group(tmp_path, monkeypatch, work_root):
    """A stop reaches what the job started (yt-dlp, ffmpeg), not only the job, and its work dir goes."""
    monkeypatch.setattr(library, "BOOKS", tmp_path / "books")
    (tmp_path / "books" / "b").mkdir(parents=True)
    fake = tmp_path / "add_book.py"
    fake.write_text(
        textwrap.dedent("""
            import os, subprocess, sys, time
            from pathlib import Path

            w = Path(os.environ["READSYNC_WORK"]) / sys.argv[1]
            w.mkdir(parents=True)
            child = subprocess.Popen(["sleep", "60"])
            (w / "part01.webm").write_bytes(b"part")
            (w / "child.tmp").write_text(str(child.pid))
            os.replace(w / "child.tmp", w / "child.pid")
            time.sleep(60)
        """),
        encoding="utf-8",
    )
    library.launch("b", [sys.executable, str(fake), "b"])
    job = library.JOBS["b"]["proc"]
    pid_file = work_root / "b" / "child.pid"
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pid_file.read_text())

        library.stop_job("b")

        assert job.poll() is not None
        assert gone(child)
        assert not (work_root / "b").exists()
        assert "b" not in library.JOBS
    finally:
        library.JOBS.pop("b", None)
        if job.poll() is None:
            os.killpg(job.pid, 9)
            job.wait()


def test_audio_ref_becomes_a_pipeline_flag(tmp_path, monkeypatch):
    """A recording picked from the audio search reaches add_book as --audio-ref, the narrator beside it."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "book.json").write_text("{}", encoding="utf-8")
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append((slug, cmd)))
    ref = "knigavuhe:50486:puteshestvie-na-zapad-1"
    form = {"slug": {"value": "b"}, "audio_ref": {"value": ref}, "narrator": {"value": "Кир Дмитриев"}}

    job, err = library.start_job(form)

    assert (job, err) == ({"slug": "b"}, "")
    ((slug, cmd),) = launched
    assert slug == "b"
    assert cmd[cmd.index("--audio-ref") + 1] == ref
    assert cmd[cmd.index("--narrator") + 1] == "Кир Дмитриев"
    assert "--audio" not in cmd


def test_a_bad_audio_ref_is_rejected(tmp_path, monkeypatch):
    """Only a ref the audio sources know is passed on: anything else is the client's mistake."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "book.json").write_text("{}", encoding="utf-8")
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append(cmd))
    for ref in ("https://evil.example/a.mp3", "knigavuhe:1:../x", "yt:short", "ia:"):
        job, err = library.start_job({"slug": {"value": "b"}, "audio_ref": {"value": ref}})
        assert job is None and err, ref
    assert launched == []
    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == ["book.json"]


def test_an_audiobook_read_as_pages_counts_its_page(tmp_path, monkeypatch):
    """Left in page mode, the book is finished by its last page, not by a narrator that never moved."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "load_state", lambda slug: STATES[slug])
    STATES = {
        "listened": {"pos": 3590, "sentPct": 10, "mode": "audio"},
        "read": {"pos": 0, "sentPct": 99, "mode": "pages"},
    }
    for slug in STATES:
        d = tmp_path / slug
        d.mkdir()
        (d / "book.toml").write_text(f'title = "{slug}"\n', encoding="utf-8")
        (d / "book.json").write_text('{"blocks": []}', encoding="utf-8")
        (d / "timing.json").write_text('{"duration": 3600, "words": []}', encoding="utf-8")
    state = {b["slug"]: b["state"] for b in library.list_books()}
    assert state["listened"]["finished"] and state["listened"]["mode"] == "audio"
    assert state["read"]["finished"] and state["read"]["mode"] == "pages"
