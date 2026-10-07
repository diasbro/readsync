"""Books on disk: what a rename keeps and what it lets go. No network."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import threading
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


def wait_job(slug: str, timeout: float = 10.0) -> None:
    proc = library.JOBS[slug]["proc"]
    proc.wait(timeout=timeout)


def fake_job(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "job.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(script)]


def test_a_failure_and_its_reason_outlive_the_server(tmp_path, monkeypatch):
    """The job writes its exit code to its log: a server started later still says why it failed."""
    monkeypatch.setattr(library, "BOOKS", tmp_path / "books")
    (tmp_path / "books" / "b").mkdir(parents=True)
    library.launch("b", fake_job(tmp_path, 'import sys\nprint("часть 1/2")\nsys.exit("в файле всего 25 слов")\n'))
    wait_job("b")
    library.JOBS.clear()  # a restart: nothing in memory

    st = library.job_status()["b"]

    assert st["running"] is False and st["exit"] == 1
    assert st["log"][-1] == "в файле всего 25 слов"
    assert (tmp_path / "books" / "b" / "add.log").exists()  # kept until the reader dismisses it
    library.stop_job("b")  # the dismissal
    assert not (tmp_path / "books" / "b" / "add.log").exists()
    assert "b" not in library.job_status()


def test_a_job_that_succeeded_unseen_lets_go_of_its_log_and_its_title(tmp_path, monkeypatch):
    books = tmp_path / "books"
    monkeypatch.setattr(library, "BOOKS", books)
    monkeypatch.setattr(library, "WISHLIST_FILE", books / "wishlist.json")
    (books / "b").mkdir(parents=True)
    (books / "wishlist.json").write_text(
        json.dumps([{"id": "w1", "title": "B", "slug": "b"}, {"id": "w2", "title": "C"}]), encoding="utf-8"
    )
    library.launch("b", fake_job(tmp_path, 'print("размечаю")\n'))
    wait_job("b")
    library.JOBS.clear()

    st = library.job_status()["b"]

    assert st["exit"] == 0
    assert not (books / "b" / "add.log").exists()
    assert [w["id"] for w in library.load_wishlist()] == ["w2"]


def test_a_job_left_by_an_earlier_server_is_still_running_and_can_be_stopped(tmp_path, monkeypatch, work_root):
    """It holds its work dir: the card says «загружается», a second load is refused, and a stop reaches it."""
    books = tmp_path / "books"
    monkeypatch.setattr(library, "BOOKS", books)
    (books / "b").mkdir(parents=True)
    (books / "b" / "book.toml").write_text('title = "Бэ"\n', encoding="utf-8")
    cmd = fake_job(
        tmp_path,
        """
        import os, sys, time
        from pathlib import Path
        w = Path(os.environ["READSYNC_WORK"]) / "b"
        w.mkdir(parents=True)
        (w / "pid").write_text(str(os.getpid()))
        print("часть 3/12", flush=True)
        time.sleep(60)
        """,
    )
    library.launch("b", cmd)
    proc = library.JOBS.pop("b")["proc"]  # the server that started it is gone
    threading.Thread(target=proc.wait, daemon=True).start()  # reaped as launchd would reap it
    try:
        deadline = time.monotonic() + 10
        while not (work_root / "b" / "pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)

        st = library.job_status()["b"]
        assert st["running"] is True and st["stage"] == "часть 3/12"
        assert library.list_books()[0]["building"] is True
        job, err = library.start_job({"slug": {"value": "b"}, "text_url": {"value": "https://example.org/b"}})
        assert job is None and err == "«Бэ» уже загружается"

        library.stop_job("b")

        assert proc.wait(timeout=15) is not None
        assert not (books / "b").exists()  # a new book called off goes altogether
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
            proc.wait()


def test_a_job_that_died_with_the_mac_reads_as_cut_short(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "add.log").write_text("+ yt-dlp …\nчасть 2/9\n", encoding="utf-8")

    st = library.job_status()["b"]

    assert st == {**st, "running": False, "exit": -1}
    assert st["log"][-1] == "оборвалась: часть 2/9"


def test_a_ready_book_called_off_keeps_its_text(tmp_path, monkeypatch, work_root):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "book.json").write_text("{}", encoding="utf-8")
    library.launch("b", fake_job(tmp_path, "import time\ntime.sleep(60)\n"))

    library.stop_job("b")

    assert sorted(p.name for p in (tmp_path / "b").iterdir()) == ["book.json"]


def test_a_book_added_by_link_takes_its_own_title(tmp_path, monkeypatch):
    """The stub names no title: add_book keeps a title it finds in book.toml, and the slug is not one."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "launch", lambda slug, cmd: None)

    job, err = library.start_job({"text_url": {"value": "https://fantasy-worlds.net/lib/id26447/"}})

    assert err == ""
    assert "title" not in (tmp_path / job["slug"] / "book.toml").read_text(encoding="utf-8")


def test_hits_keep_the_count_of_unopenable_editions(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    (tmp_path / "b").mkdir()
    library.save_hits("b", {"hits": [], "unopenable": 3, "query": "b"})
    assert json.loads((tmp_path / "b" / "hits.json").read_text(encoding="utf-8"))["unopenable"] == 3
