"""Merging the parts of a book: a part with its own title is not announced twice (F31)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from merge_books import merge  # noqa: E402

PIPE = Path(__file__).resolve().parent.parent / "pipeline"


def block(kind: str, text: str, chapter: int = 0, notes: list | None = None) -> dict:
    return {
        "images": [],
        "id": f"b-{text}",
        "kind": kind,
        "chapter": chapter,
        "stanza": None,
        "text": text,
        "em": [],
        "notes": notes or [],
        "sentences": [[0, len(text)]],
        "audio": True,
    }


def part(title: str, first: dict) -> dict:
    return {
        "title": title,
        "author": "А",
        "chapters": [{"id": "s1", "title": first["text"], "level": 1, "first_block": 0}],
        "blocks": [first, block("p", "Текст части.")],
        "notes": {},
    }


def test_a_part_with_its_own_title_gets_no_second_one():
    m = merge([("", part("Том 1", block("title", "Книга первая"))), ("", part("Том 2", block("p", "Начало.")))])

    assert [(b["kind"], b["text"]) for b in m["blocks"]] == [
        ("title", "Книга первая"),
        ("p", "Текст части."),
        ("title", "Том 2"),  # the part brings no heading of its own: one is read out
        ("p", "Начало."),
        ("p", "Текст части."),
    ]
    assert [(c["title"], c["level"], c["first_block"]) for c in m["chapters"]] == [
        ("Том 1", 1, 0),  # the contents still group the volumes
        ("Книга первая", 2, 0),
        ("Том 2", 1, 2),
        ("Начало.", 2, 3),
    ]
    assert [b["chapter"] for b in m["blocks"]] == [1, 1, 2, 3, 3]


def test_the_note_marker_survives_the_merge():
    a = part("Том 1", block("p", "Кит.", notes=[{"pos": 3, "id": "n1", "m": "1"}]))
    a["notes"] = {"n1": "сноска"}
    m = merge([("", a), ("", part("Том 2", block("p", "Б.")))])
    assert m["blocks"][1]["notes"] == [{"pos": 3, "id": "p1_n1", "m": "1"}]


def test_the_merged_book_is_written_in_the_contracts_key_order(tmp_path):
    for i, p in enumerate((part("Том 1", block("title", "Книга первая")), part("Том 2", block("p", "Б."))), 1):
        d = tmp_path / "parts" / f"{i:02d}"
        d.mkdir(parents=True)
        p["blocks"][1]["st"] = {}  # an empty optional key is left out
        p["blocks"][1] = dict(reversed(list(p["blocks"][1].items())))
        (d / "book.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")

    subprocess.run([sys.executable, str(PIPE / "merge_books.py"), str(tmp_path)], check=True, capture_output=True)

    blocks = json.loads((tmp_path / "book.json").read_text(encoding="utf-8"))["blocks"]
    assert list(blocks[1]) == ["images", "id", "kind", "chapter", "stanza", "text", "em", "notes", "sentences", "audio"]
