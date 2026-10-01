"""Books on disk: what a rename keeps and what it lets go. No network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import library


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
