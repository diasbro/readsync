"""What add_book stamps into book.toml when it finishes. No network: every stage is stubbed."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import add_book  # noqa: E402


def edition(d: Path) -> str:
    return tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))["edition"]


def finished_book(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(add_book, "BOOKS", tmp_path)
    monkeypatch.setattr(add_book, "run", lambda cmd, **kw: None)
    monkeypatch.setattr(add_book, "tidy", lambda d: None)
    monkeypatch.setattr(add_book, "build_audio", lambda src, d, lang: (d / "audio.m4a").write_bytes(b"a" * 10))
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
