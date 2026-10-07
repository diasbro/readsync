"""Books on disk: what a rename keeps and what it lets go. No network."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
import tomllib
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
    assert "--narrator=Кир Дмитриев" in cmd  # one argument, so a value starting with "-" is never an option
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
    assert state["listened"]["atEnd"] and state["listened"]["status"] == "done" and state["listened"]["mode"] == "audio"
    assert state["read"]["atEnd"] and state["read"]["status"] == "done" and state["read"]["mode"] == "pages"


def test_books_carry_their_status_and_finish_date(tmp_path, monkeypatch):
    """The library shows the derived status; the old shelf value «library» at the end of the book is paused."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "load_state", lambda slug: STATES[slug])
    STATES = {
        "done": {"shelf": "done", "shelfAt": 5, "finished": ["2026-03-12", "2026-10-07"], "finishedAt": 5},
        "old-shelf": {"shelf": "library", "shelfAt": 9, "sent": 50, "sentAt": 3, "sentPct": 100},
        "again": {"shelf": "reading", "shelfAt": 9, "finished": ["2026-03-12"], "finishedAt": 5},
        "fresh": {},
    }
    for slug in STATES:
        d = tmp_path / slug
        d.mkdir()
        (d / "book.toml").write_text(f'title = "{slug}"\ntext_end = 40\n', encoding="utf-8")
        (d / "book.json").write_text('{"blocks": []}', encoding="utf-8")
    state = {b["slug"]: b["state"] for b in library.list_books()}
    assert (state["done"]["status"], state["done"]["finishedOn"], state["done"]["finished"]) == (
        "done",
        "2026-10-07",
        ["2026-03-12", "2026-10-07"],
    )
    assert (state["old-shelf"]["status"], state["old-shelf"]["atEnd"]) == ("paused", True)
    assert (state["again"]["status"], state["again"]["rereading"]) == ("reading", True)
    assert (state["fresh"]["status"], state["fresh"]["finishedOn"], state["fresh"]["finished"]) == ("none", None, [])
    assert {b["slug"]: b.get("text_end") for b in library.list_books()}["fresh"] == 40


def test_manifests_add_the_end_of_the_text_once_and_nothing_else(tmp_path, monkeypatch):
    """A book stamped before text_end existed gets only the two keys: id, edition, files and the file's time
    stay, so the phone does not see a new version, and the library does not see a new book."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    d = tmp_path / "old"
    d.mkdir()
    toml = 'title = "T"\nid = "{}"\nedition = "{}"\nfiles = "book.json:1"\n'.format("a" * 32, "b" * 32)
    (d / "book.toml").write_text(toml, encoding="utf-8")
    book = {"chapters": [{"title": "Глава", "first_block": 0}], "blocks": [{"sentences": [[0, 1]] * 10}]}
    (d / "book.json").write_text(json.dumps(book), encoding="utf-8")
    (d / "timing.json").write_text('{"duration": 100, "words": [[0, 0, 1, 1.0, 95.5]]}', encoding="utf-8")
    os.utime(d / "book.toml", (1_000_000, 1_000_000))

    library.ensure_manifests()

    text = (d / "book.toml").read_text(encoding="utf-8")
    assert text == toml + "text_end = 10\naudio_end = 95.50\n"
    assert (d / "book.toml").stat().st_mtime == 1_000_000
    (d / "book.toml").write_text(toml + "text_end = 3\n", encoding="utf-8")
    library.ensure_manifests()  # already there: left alone
    assert (d / "book.toml").read_text(encoding="utf-8") == toml + "text_end = 3\n"


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


def ready_book(tmp_path: Path, monkeypatch, slug: str = "b", toml: str = 'title = "B"\nedition = "e1"\n') -> Path:
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = tmp_path / slug
    d.mkdir()
    (d / "book.toml").write_text(toml, encoding="utf-8")
    (d / "book.json").write_text('{"title": "B", "blocks": []}', encoding="utf-8")
    return d


def test_an_uploaded_recording_goes_with_the_job(tmp_path, monkeypatch):
    """The upload is a download like any other: tidied once the job is over, never synced as part of the book."""
    library._pipeline()
    from tidy import tidy

    d = ready_book(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append(cmd))

    job, err = library.start_job({"slug": {"value": "b"}, "audio_file": {"filename": "Book.MP3", "data": b"x" * 10}})

    assert err == ""
    (upload,) = [a for a in launched[0] if a.startswith(str(d))]
    assert Path(upload).exists()
    tidy(d)
    assert not Path(upload).exists()


def test_an_upload_name_never_reaches_ffmpegs_list(tmp_path, monkeypatch):
    """The parts list quotes each file in '…': a quote or a line break in the extension would break it."""
    d = ready_book(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append(cmd))

    library.start_job({"slug": {"value": "b"}, "audio_file": {"filename": "x.m4a'\nfile '/etc/x", "data": b"x"}})

    (upload,) = [a for a in launched[0] if a.startswith(str(d))]
    assert re.fullmatch(r"upload_audio\.[a-z0-9]+", Path(upload).name), upload


def test_a_replacement_keeps_the_page_until_it_is_in(tmp_path, monkeypatch):
    """A text replacement that fails or is called off changes nothing: the page position is still there.
    Once the new text is in, its new edition drops the old sentence anyway."""
    import state

    d = ready_book(tmp_path, monkeypatch)
    state.put(d, {"sent": 1234, "sentAt": 5, "sentPct": 40})
    monkeypatch.setattr(library, "launch", lambda slug, cmd: None)  # and the job then fails

    job, err = library.start_job(
        {"slug": {"value": "b"}, "text_url": {"value": "https://example.org/x.fb2"}, "replace": {"value": "1"}}
    )

    assert err == ""
    assert library.load_state("b")["sent"] == 1234
    (d / "book.toml").write_text('title = "B"\nedition = "e2"\n', encoding="utf-8")  # the job succeeded after all
    assert "sent" not in library.load_state("b")


def test_a_refused_form_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "launch", lambda slug, cmd: pytest.fail("launched"))
    form = {
        "slug": {"value": "nb"},
        "text_file": {"filename": "book.fb2", "data": b"<FictionBook/>"},
        "audio_url": {"value": "ftp://example.org/a.mp3"},
    }

    job, err = library.start_job(form)

    assert job is None and err == "ссылка на аудио должна начинаться с http(s)"
    assert not (tmp_path / "nb").exists()


def test_a_book_keeps_its_place_in_the_library_when_renamed(tmp_path, monkeypatch):
    """New books come first by the time they were added; a rename does not make a book new."""
    d = ready_book(tmp_path, monkeypatch)
    os.utime(d / "book.toml", (1_000_000_000, 1_000_000_000))

    library.rename_book("b", "Другое название")

    (book,) = library.list_books()
    assert book["title"] == "Другое название"
    assert book["added"] == 1_000_000_000_000


def test_a_book_is_not_deleted_under_a_job_that_just_started(tmp_path, monkeypatch):
    """The server starts a job under STATE_LOCK: a delete waiting for the lock must see that job."""
    d = ready_book(tmp_path, monkeypatch)
    procs = []

    def launch(slug, cmd):
        procs.append(subprocess.Popen(["sleep", "5"]))
        library.JOBS[slug] = {"proc": procs[-1], "started": time.time(), "slug": slug}

    monkeypatch.setattr(library, "launch", launch)
    errors = []

    def delete():
        try:
            library.delete_book("b")
        except ValueError as e:
            errors.append(str(e))

    t = threading.Thread(target=delete)
    try:
        with library.STATE_LOCK:  # as serve.do_POST holds it around start_job
            t.start()
            time.sleep(0.2)  # the delete is waiting for the lock by now
            job, err = library.start_job({"slug": {"value": "b"}, "audio_url": {"value": "https://example.org/a"}})
            assert err == ""
        t.join(5)
        assert errors == ["книга ещё загружается"]
        assert d.is_dir()
    finally:
        for p in procs:
            p.kill()
            p.wait()
        library.JOBS.clear()


def test_a_title_with_a_line_break_keeps_book_toml_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "launch", lambda slug, cmd: None)

    job, err = library.start_job(
        {"title": {"value": "Война и мир.\n Том 1\x07"}, "text_url": {"value": "https://example.org/x.fb2"}}
    )

    assert err == ""
    meta = tomllib.loads((tmp_path / job["slug"] / "book.toml").read_text(encoding="utf-8"))
    assert meta["title"] == "Война и мир.\n Том 1\x07"


def test_a_link_with_a_comma_stays_one_link(tmp_path, monkeypatch):
    """Wikisource keeps commas in its titles: «Hamlet, Prince of Denmark» is one link, not two."""
    from sources.wikisource import Wikisource

    monkeypatch.setattr(library, "BOOKS", tmp_path)
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append(cmd))
    (hit,) = Wikisource().pages({"query": {"pages": [{"title": "Hamlet, Prince of Denmark", "index": 1}]}}, "en")

    job, err = library.start_job({"title": {"value": "Hamlet"}, "text_url": {"value": hit["url"]}})

    assert err == ""
    assert launched[0][launched[0].index("--text") + 1] == hit["url"]
    assert library.form_values({"audio_url": {"value": "https://a/1\nhttps://a/2 https://a/3"}}, "audio_url") == [
        "https://a/1",
        "https://a/2",
        "https://a/3",
    ]


def test_a_position_that_is_not_a_number_does_not_break_the_library(tmp_path, monkeypatch):
    """One device file holding a string (a phone build with a bug, a hand edit) is read as no position."""
    import state

    d = ready_book(tmp_path, monkeypatch)
    book = {
        "title": "B",
        "chapters": [{"title": "I"}],
        "blocks": [{"kind": "p", "chapter": 0, "text": "Раз. Два.", "sentences": [[0, 4], [5, 9]]}],
    }
    (d / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    state.put(d, {"pos": "x", "posAt": 1, "sent": "x", "sentAt": 1, "sentPct": "99"})

    (listed,) = library.list_books()

    assert listed["state"]["pos"] == 0 and not listed["state"]["atEnd"]
    assert library.where_now("b")["text"] == "Раз."


def test_a_rename_keeps_no_control_characters(tmp_path, monkeypatch):
    """A pasted title may carry a bell or a line break: the title is one line of text, book.toml stays readable."""
    d = ready_book(tmp_path, monkeypatch)

    library.rename_book("b", "Новое\x07 название\n")

    assert tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))["title"] == "Новое название"


def test_a_new_book_named_like_one_in_the_library_gets_its_own_folder(tmp_path, monkeypatch):
    """Two books may share a title (another author, a title the slug cannot tell apart): the second is not refused."""
    ready_book(tmp_path, monkeypatch, slug="rasskazy")
    monkeypatch.setattr(library, "launch", lambda slug, cmd: None)
    form = {
        "title": {"value": "Рассказы"},
        "author": {"value": "Чехов"},
        "text_url": {"value": "https://example.org/1"},
    }

    job, err = library.start_job(form)
    assert (job, err) == ({"slug": "rasskazy-2"}, "")
    (tmp_path / "rasskazy-2" / "book.json").write_text("{}", encoding="utf-8")
    assert library.start_job(form) == ({"slug": "rasskazy-3"}, "")
    assert 'title = "B"' in (tmp_path / "rasskazy" / "book.toml").read_text(encoding="utf-8")


def test_a_saved_title_keeps_the_catalogs_its_search_missed(tmp_path, monkeypatch):
    """After a reload the card still says the search failed, not that no catalog has the book."""
    monkeypatch.setattr(library, "WISHLIST_FILE", tmp_path / "wishlist.json")
    (item,) = library.wishlist_add({"title": "Дао Дэ Цзин"})

    library.wishlist_update(item["id"], {"hits": [], "searched": "2026-10-07", "failed": ["Флибуста", "Coollib"]})

    assert library.load_wishlist()[0]["failed"] == ["Флибуста", "Coollib"]


def test_a_second_book_of_the_same_title_while_the_first_loads_gets_its_own_folder(tmp_path, monkeypatch):
    """The first is only a stub book.toml while its job runs: its folder is taken all the same."""
    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "launch", lambda slug, cmd: None)
    library._pipeline()
    from tidy import claim, work_dir

    (tmp_path / "kniga").mkdir()
    (tmp_path / "kniga" / "book.toml").write_text('slug = "kniga"\ntitle = "Kniga"\n', encoding="utf-8")
    claim(work_dir("kniga"))  # its job, loading

    job, err = library.start_job({"title": {"value": "Kniga"}, "text_url": {"value": "https://example.org/b2"}})

    assert (job, err) == ({"slug": "kniga-2"}, "")
    assert (tmp_path / "kniga" / "book.toml").read_text(encoding="utf-8") == 'slug = "kniga"\ntitle = "Kniga"\n'


def test_a_rename_waits_for_the_job_holding_the_book(tmp_path, monkeypatch):
    """A job (or a reextract) rewrites book.toml itself: a rename meanwhile would be lost, so it is refused."""
    d = ready_book(tmp_path, monkeypatch)
    library._pipeline()
    from tidy import claim, work_dir

    w = claim(work_dir("b"))
    with pytest.raises(library.Busy, match="обрабатывается"):
        library.rename_book("b", "Новое")
    assert 'title = "B"' in (d / "book.toml").read_text(encoding="utf-8")
    (w / "pid").write_text("1", encoding="utf-8")  # the job is over
    library.rename_book("b", "Новое")
    assert 'title = "Новое"' in (d / "book.toml").read_text(encoding="utf-8")


def test_slugify_reads_combining_letters_as_themselves():
    """W27: NFC first, then each code point; the cut keeps a dash it lands on (the phone does the same)."""
    assert library.slugify("Мастер и Маргарита") == "master-i-margarita"
    assert library.slugify("Чаи\u0306ка") == library.slugify("Чайка") == "chayka"
    assert library.slugify("е\u0301ль") == "e-l"  # е with a stress mark: no letter of its own
    assert library.slugify("a" * 47 + " b") == "a" * 47 + "-"
    assert library.slugify("!!!") == "book"
