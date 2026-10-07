"""What add_book stamps into book.toml when it finishes. No network: every stage is stubbed."""

from __future__ import annotations

import io
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
        add_book, "build_text", lambda src, d, t, a, w: (d / "book.json").write_text(json.dumps({"blocks": []}))
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

    def stub(src, d, t, a, w):
        (d / "parts").mkdir()  # as an older version left them
        (d / "parts" / "1.fb2").write_text("Книга заблокирована.", encoding="utf-8")
        raise SystemExit("на сайте вместо книги заглушка")

    monkeypatch.setattr(add_book, "build_text", stub)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--text", "https://example.org/b"])
    with pytest.raises(SystemExit):
        add_book.main()
    assert not (tmp_path / "b" / "parts").exists()


def test_a_new_book_that_failed_leaves_no_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)

    def blocked(src, d, t, a, w):
        raise SystemExit("на сайте вместо книги заглушка")

    monkeypatch.setattr(add_book, "build_text", blocked)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "nb", "--text", "https://example.org/b"])
    with pytest.raises(SystemExit):
        add_book.main()
    assert not (tmp_path / "nb").exists()


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


def test_audio_ref_downloads_parts_into_work_dir(tmp_path, monkeypatch, work_root):
    """A found recording is resolved, downloaded part by part into the work dir and built there: the book
    folder gets nothing new until the finished audio and timing land."""
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)
    d = tmp_path / "b"
    d.mkdir()
    (d / "book.json").write_text(json.dumps({"title": "B", "blocks": []}), encoding="utf-8")
    (d / "book.toml").write_text(f'title = "B"\nid = "{"a" * 32}"\nedition = "e1"\n', encoding="utf-8")
    before = sorted(p.name for p in d.iterdir())
    ref = "knigavuhe:50486:puteshestvie-na-zapad-1"
    parts = [
        {"title": f"{i:02d}", "duration": 60.0, "url": f"https://s1.knigavuhe.org/{i}.mp3", "size": None}
        for i in (1, 2)
    ]
    monkeypatch.setattr(add_book, "recording", lambda r: ("knigavuhe", parts) if r == ref else pytest.fail(r))
    downloads, seen_in_book = [], []

    def download(url, dst):
        assert dst.parent == work_root / "b"
        downloads.append(url)
        seen_in_book.append(sorted(p.name for p in d.iterdir()))
        dst.write_bytes(b"mp3")

    def run(cmd, **kw):
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"audio")
        elif str(cmd[1]).endswith("timing_from_anchors.py"):
            (Path(cmd[2]) / "timing.json").write_text('{"words": []}', encoding="utf-8")
        seen_in_book.append(sorted(p.name for p in d.iterdir()))

    land_audio = add_book.land_audio

    def landing(w, book):
        seen_in_book.append(sorted(p.name for p in d.iterdir()))
        assert sorted(p.name for p in w.glob("part*")) == []  # the parts went once joined
        land_audio(w, book)

    monkeypatch.setattr(add_book, "download", download)
    monkeypatch.setattr(add_book, "run", run)
    monkeypatch.setattr(add_book, "duration_of", lambda p: 60.0)
    monkeypatch.setattr(add_book, "aac_args", lambda: [])
    monkeypatch.setattr(add_book, "land_audio", landing)
    monkeypatch.setattr(add_book.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        sys, "argv", ["add_book.py", "b", "--audio-ref", ref, "--narrator", "Кир Дмитриев", "--no-align"]
    )

    add_book.main()

    assert downloads == [p["url"] for p in parts]
    assert seen_in_book and all(names == before for names in seen_in_book)
    assert {"audio.m4a", "timing.json"} <= {p.name for p in d.iterdir()}
    meta = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    assert meta["audio_source"] == ref and meta["narrator"] == "Кир Дмитриев"
    assert not (work_root / "b").exists()


def test_download_resumes_a_cut_part(tmp_path, monkeypatch):
    """A connection cut mid-part is picked up by Range; the part is renamed only once it is whole."""
    body = bytes(range(100))
    ranges = []

    class Resp:
        def __init__(self, data, status, length, cut):
            self.data, self.status, self.cut = data, status, cut
            self.headers = {"Content-Length": str(length)}
            if status == 206:
                self.headers["Content-Range"] = f"bytes {len(body) - length}-{len(body) - 1}/{len(body)}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://s1.knigavuhe.org/1.mp3"

        def read(self, n):
            if not self.data and self.cut:
                raise ConnectionResetError("reset")
            out, self.data = self.data[:n], self.data[n:]
            return out

    def urlopen(req, timeout):
        rng = req.get_header("Range")
        ranges.append(rng)
        if rng is None:  # the first try is cut after 40 bytes
            return Resp(body[:40], 200, len(body), cut=True)
        start = int(rng.removeprefix("bytes=").rstrip("-"))
        return Resp(body[start:], 206, len(body) - start, cut=False)

    monkeypatch.setattr(add_book.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(add_book.time, "sleep", lambda s: None)
    dst = tmp_path / "part01.mp3"
    add_book.download("https://s1.knigavuhe.org/1.mp3", dst)
    assert dst.read_bytes() == body
    assert ranges == [None, "bytes=40-"]
    assert not (tmp_path / "part01.mp3.part").exists()


def test_a_file_packed_twice_is_unpacked():
    """coollib serves a pdf as a zip inside a zip; an fb2 in a zip stays packed for its own reader."""
    import io
    import zipfile

    def packed(name: str, data: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, data)
        return buf.getvalue()

    pdf = b"%PDF-1.4 a book"
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("book.pdf", pdf)
        z.writestr("book.fbd", b"<FictionBook/>")  # the catalog's description beside it
    data, name = add_book.unwrap(packed("book.zip", inner.getvalue()), "https://x/b/1/fb2")
    assert (data, name) == (pdf, "book.pdf")
    assert add_book.sniff(data, name) == "pdf"
    fb2zip = packed("book.fb2", b"<FictionBook/>")
    assert add_book.unwrap(fb2zip, "b.fb2.zip") == (fb2zip, "b.fb2.zip")


def book_with_audio(tmp_path: Path, monkeypatch) -> Path:
    """A finished audiobook: text, audio, its captions and timing, stamped."""
    from manifest import stamp

    monkeypatch.setattr(add_book, "BOOKS", tmp_path)
    d = tmp_path / "b"
    d.mkdir()
    (d / "book.json").write_text(json.dumps({"title": "Old", "blocks": []}), encoding="utf-8")
    (d / "audio.m4a").write_bytes(b"audio" * 10)
    (d / "timing.json").write_text('{"words": [[0, 0, 1, 0.0, 0.5]]}', encoding="utf-8")
    (d / "yt.merged.json3").write_text('{"events": []}', encoding="utf-8")
    (d / "book.toml").write_text('title = "B"\n', encoding="utf-8")
    stamp(d)
    return d


def new_text(src, d, title, author, w):
    """build_text as it ends: the merged book and its pictures in the work dir."""
    (w / "images").mkdir()
    (w / "images" / "cover.jpg").write_bytes(b"new cover")
    (w / "book.json").write_text(json.dumps({"title": "New", "blocks": [{"text": "x" * 500}]}), encoding="utf-8")


def test_a_text_replacement_that_fails_leaves_the_book_as_it_was(tmp_path, monkeypatch):
    """The new text is timed against the old captions; when that fails (or the reader stops it) the book keeps
    its text, timing and edition, and the manifest still matches the files."""
    d = book_with_audio(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in d.iterdir()}

    def run(cmd, **kw):
        if Path(cmd[1]).name == "anchors.py":
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(add_book, "build_text", new_text)
    monkeypatch.setattr(add_book, "run", run)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--text", "https://example.org/x", "--no-align"])
    with pytest.raises(subprocess.CalledProcessError):
        add_book.main()

    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_a_new_text_and_its_pictures_land_last(tmp_path, monkeypatch, work_root):
    """Until the timing for the new text is ready the book shows the old one; then text, pictures and a new
    edition come in together."""
    d = book_with_audio(tmp_path, monkeypatch)
    old = edition(d)
    seen = []

    def run(cmd, **kw):
        if Path(cmd[1]).name == "anchors.py":  # it times the new text, which waits in the work dir
            seen.append((json.loads((work_root / "b" / "book.json").read_text())["title"], book_title(d)))
        if str(cmd[1]).endswith("timing_from_anchors.py"):
            (Path(cmd[2]) / "timing.json").write_text('{"words": []}', encoding="utf-8")

    def book_title(book: Path) -> str:
        return json.loads((book / "book.json").read_text(encoding="utf-8"))["title"]

    monkeypatch.setattr(add_book, "build_text", new_text)
    monkeypatch.setattr(add_book, "run", run)
    monkeypatch.setattr(sys, "argv", ["add_book.py", "b", "--text", "https://example.org/x", "--no-align"])
    add_book.main()

    assert seen == [("New", "Old")]
    assert book_title(d) == "New" and (d / "images" / "cover.jpg").read_bytes() == b"new cover"
    assert (d / "timing.json").read_text(encoding="utf-8") == '{"words": []}'
    assert edition(d) != old
    meta = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    assert f"book.json:{(d / 'book.json').stat().st_size}" in meta["files"]


def test_a_title_with_a_line_break_keeps_book_toml_readable(tmp_path, monkeypatch):
    """An FB2 title may span lines: it is kept on one, and book.toml must parse, or the book drops out of the library."""
    from extract_fb2 import extract

    src = tmp_path / "x" / "book.fb2"
    src.parent.mkdir()
    src.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Война и мир.\n Том 1</book-title></title-info></description>"
        "<body><section><p>Текст.</p></section></body></FictionBook>",
        encoding="utf-8",
    )
    book = extract(src)
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)
    monkeypatch.setattr(add_book, "run", lambda cmd, **kw: None)
    monkeypatch.setattr(
        add_book,
        "build_text",
        lambda s, d, t, a, w: (d / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8"),
    )
    (tmp_path / "nb").mkdir()
    (tmp_path / "nb" / "book.toml").write_text('slug = "nb"\n', encoding="utf-8")  # the server's stub
    monkeypatch.setattr(sys, "argv", ["add_book.py", "nb", "--text", "https://example.org/x.fb2", "--no-align"])

    add_book.main()

    assert book["title"] == "Война и мир.\n Том 1"
    assert tomllib.loads((tmp_path / "nb" / "book.toml").read_text(encoding="utf-8"))["title"] == "Война и мир. Том 1"


def test_a_title_that_starts_with_a_dash_reaches_the_job(tmp_path, monkeypatch):
    """The server's command line for add_book: «-273» is a title, not an option."""
    import library

    monkeypatch.setattr(library, "BOOKS", tmp_path)
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)
    launched = []
    monkeypatch.setattr(library, "launch", lambda slug, cmd: launched.append(cmd))
    job, err = library.start_job(
        {"title": {"value": "-273"}, "author": {"value": "--"}, "text_url": {"value": "https://example.org/x.fb2"}}
    )
    assert err == ""
    got = []
    monkeypatch.setattr(add_book, "build", lambda args, d, w: got.append(args))
    monkeypatch.setattr(sys, "argv", launched[0][1:])

    add_book.main()

    assert (got[0].slug, got[0].title, got[0].author) == (job["slug"], "-273", "--")


def test_image_links_of_a_page_stay_inside_its_images(tmp_path, monkeypatch):
    """data-src comes from a downloaded page: a path in it names a picture in images/, never a file elsewhere."""
    part = tmp_path / "w" / "parts" / "01"
    part.mkdir(parents=True)
    page = b'<div id="book-content"><img data-src="../../../../evil.txt"><img data-src="/abs/x.jpg"></div>'
    monkeypatch.setattr(add_book.urllib.request, "urlopen", lambda r, timeout=0: io.BytesIO(b"picture"))

    add_book.download_site_images(page, "https://evil.example/book/1/read.html", part)

    assert sorted(p.name for p in (part / "images").iterdir()) == ["evil.txt", "x.jpg"]
    assert not (tmp_path / "evil.txt").exists()


def test_a_packed_file_too_big_for_a_book_is_not_unpacked(monkeypatch):
    """A zip bomb served as a book is refused by the size it declares, before a byte is unpacked."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("book.pdf", b"%PDF-1.4 " + b"0" * 5000)
    monkeypatch.setattr(add_book, "MAX_UNPACKED", 1000)
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda *a: pytest.fail("unpacked"))

    with pytest.raises(SystemExit, match="это не книга"):
        add_book.unwrap(buf.getvalue(), "book.zip")


class Resp:
    """An HTTP answer for `urlopen` fakes: `data` is what arrives, `headers` what the server claims."""

    def __init__(self, data: bytes, status: int, headers: dict, final: str = ""):
        self.data, self.status, self.headers, self.final = data, status, headers, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.final

    def read(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out


def test_a_text_body_cut_short_is_resumed(monkeypatch, tmp_path):
    """The first answer ends at 200 of the 500 bytes it announced: the length check catches it, the rest
    comes by Range. The format comes from the address after the redirect, the body does not tell it."""
    body = b"plain words " * 42
    ranges = []

    def urlopen(req, timeout):
        rng = req.get_header("Range")
        ranges.append(rng)
        if rng is None:
            return Resp(body[:200], 200, {"Content-Length": str(len(body))}, "https://cdn.example/book.fb2")
        start = int(rng.removeprefix("bytes=").rstrip("-"))
        headers = {
            "Content-Length": str(len(body) - start),
            "Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}",
        }
        return Resp(body[start:], 206, headers, "https://cdn.example/book.fb2")

    monkeypatch.setattr(add_book.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(add_book.time, "sleep", lambda s: None)
    part = tmp_path / "01"

    assert add_book.fetch_text("https://mirror.example/get?id=1", part) == "fb2"
    assert (part / "book.fb2").read_bytes() == body
    assert ranges == [None, "bytes=200-"]


def test_a_resumed_answer_from_another_offset_starts_over(monkeypatch, tmp_path):
    """A 206 that does not continue from the bytes we have is not appended: the next try starts from zero."""
    body = bytes(range(100))
    ranges = []

    def urlopen(req, timeout):
        rng = req.get_header("Range")
        ranges.append(rng)
        if rng is None and len(ranges) == 1:
            return Resp(body[:40], 200, {"Content-Length": "100"})
        if rng is None:
            return Resp(body, 200, {"Content-Length": "100"})
        return Resp(body[10:], 206, {"Content-Length": "90", "Content-Range": "bytes 10-99/100"})

    monkeypatch.setattr(add_book.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(add_book.time, "sleep", lambda s: None)
    dst = tmp_path / "x.bin"
    add_book.download("https://x.example/x.bin", dst)
    assert dst.read_bytes() == body
    assert ranges == [None, "bytes=40-", None]


def test_a_missing_file_is_not_retried(monkeypatch, tmp_path):
    calls = []

    def urlopen(req, timeout):
        calls.append(timeout)
        raise add_book.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(add_book.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(add_book.time, "sleep", lambda s: pytest.fail("retried"))
    with pytest.raises(SystemExit, match="текст не скачался"):
        add_book.fetch_text("https://x.example/gone.epub", tmp_path / "01")
    assert calls == [120]
