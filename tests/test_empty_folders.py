"""A book that never came to be leaves no folder in the library: refused, failed to start or called off.
The `book-<time>` folders a form without a title or a link once left were made before the form was refused."""

from __future__ import annotations

import pytest

import library


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    monkeypatch.setattr(library, "BOOKS", tmp_path / "books")
    (tmp_path / "books").mkdir()
    return tmp_path / "books"


def test_a_form_without_title_or_text_leaves_no_folder(books):
    assert library.start_job({}) == (None, "нужен текст: ссылка или файл")
    assert library.start_job({"audio_url": {"value": "https://example.org/a.mp3"}})[0] is None
    assert list(books.iterdir()) == []


def test_a_job_that_cannot_start_leaves_no_folder(books, monkeypatch):
    def broken(slug, cmd):
        raise OSError("no such interpreter")

    monkeypatch.setattr(library, "launch", broken)
    with pytest.raises(OSError):
        library.start_job({"text_file": {"filename": "x.fb2", "data": b"<FictionBook/>"}})
    assert list(books.iterdir()) == []


def test_an_upload_that_cannot_be_saved_leaves_no_folder(books, monkeypatch):
    monkeypatch.setattr(library, "launch", lambda slug, cmd: pytest.fail("launched"))
    with pytest.raises(OSError):  # a name past the file system's limit
        library.start_job({"title": {"value": "Б"}, "text_file": {"filename": "я" * 300 + ".fb2", "data": b"x"}})
    assert list(books.iterdir()) == []


def test_a_job_that_cannot_start_keeps_a_book_that_was_there(books, monkeypatch):
    d = books / "b"
    d.mkdir()
    (d / "book.json").write_text("{}", encoding="utf-8")
    (d / "book.toml").write_text('title = "B"\n', encoding="utf-8")
    monkeypatch.setattr(library, "launch", lambda slug, cmd: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        library.start_job({"slug": {"value": "b"}, "audio_url": {"value": "https://example.org/a.mp3"}})
    assert (d / "book.json").exists() and (d / "book.toml").exists()
