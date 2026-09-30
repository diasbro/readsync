"""Books on disk: what a rename keeps and what it lets go. No network."""

from __future__ import annotations

import json

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
