"""Where the main text ends, on small books made up for each rule, and how manifest.py stamps it."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from manifest import stamp  # noqa: E402
from text_end import audio_end, text_end  # noqa: E402


def make(*chapters: tuple[str, int] | tuple[str, int, int]) -> dict:
    """A book of one block per chapter: (title, sentences[, level])."""
    book: dict = {"chapters": [], "blocks": []}
    for i, (title, n, *level) in enumerate(chapters):
        book["chapters"].append({"title": title, "level": level[0] if level else 1, "first_block": i})
        book["blocks"].append({"chapter": i, "sentences": [[k, k + 1] for k in range(n)]})
    return book


def test_a_bibliography_at_the_back_is_not_main_text():
    assert text_end(make(("Глава 1", 500), ("Глава 2", 400), ("Библиография", 100))) == 900


def test_back_matter_runs_back_through_notes_indexes_and_acknowledgements():
    book = make(
        ("Глава", 900),
        ("Заключение", 40),
        ("Благодарности", 5),
        ("Об авторах", 4),
        ("Избранная библиография", 30),
        ("Над книгой работали", 2),
    )
    assert text_end(book) == 940


def test_a_dictionary_by_letters_is_cut_cz_and_chzh_included():
    letters = [
        ("Б", 20, 2),
        ("Ц", 20, 2),
        ("Цз", 20, 2),
        ("Ч", 20, 2),
        ("Чж", 20, 2),
        ("Ш", 15, 2),
        ("Ю", 15, 2),
        ("Я", 10, 2),
    ]
    assert text_end(make(("Глава", 1000), *letters)) == 1000


def test_a_dictionarys_letters_under_an_index_heading_are_cut_with_it():
    book = make(("Глава", 1000), ("Указатель имён", 1), ("А", 40, 2), ("Б", 40, 2))
    assert text_end(book) == 1000


def test_chapters_under_a_bibliography_heading_go_with_it():
    book = make(("Глава", 1000), ("Список литературы", 1, 2), ("На русском языке", 20, 3), ("На китайском", 10, 3))
    assert text_end(book) == 1000


def test_one_short_title_at_the_end_is_a_chapter_not_a_letter():
    assert text_end(make(("Глава", 500), ("Мы", 300))) == 800


def test_a_short_epilogue_is_main_text():
    book = make(("Глава", 999), ("Эпилог", 2), ("Об авторе", 1))
    assert text_end(book) == 1001


def test_an_epilogue_stops_the_walk_even_after_tiny_chapters():
    assert text_end(make(("Глава", 999), ("Эпилог, или роза ветров", 20), ("Конец", 0))) == 1019


def test_a_large_untitled_last_section_stays():
    assert text_end(make(("Глава 20", 300), ("Конфуций", 1), ("", 400))) == 701


def test_back_matter_over_35_percent_means_the_rule_is_wrong():
    assert text_end(make(("Глава", 600), ("Комментарии", 400))) == 1000


def test_a_book_without_chapters_ends_at_its_end():
    assert text_end({"chapters": [], "blocks": [{"sentences": [[0, 1]] * 7}]}) == 7


def test_audio_ends_with_the_last_word_before_the_back_matter():
    book = make(("Глава", 90), ("Примечания", 10))
    timing = {"duration": 500.0, "words": [[0, 0, 1, 1.0, 2.0], [0, 2, 3, 380.0, 381.5], [1, 0, 1, 400.0, 401.0]]}
    assert audio_end(book, timing) == 381.5


def test_audio_with_no_word_in_the_main_text_ends_at_the_duration():
    book = make(("Глава", 90), ("Примечания", 10))
    assert audio_end(book, {"duration": 500.0, "words": [[1, 0, 1, 400.0, 401.0]]}) == 500.0


def write(d: Path, book: dict, timing: dict | None) -> None:
    d.mkdir()
    (d / "book.json").write_text(json.dumps(book), encoding="utf-8")
    if timing is not None:
        (d / "timing.json").write_text(json.dumps(timing), encoding="utf-8")


def test_stamp_writes_the_ends_and_keeps_files_and_edition(tmp_path):
    d = tmp_path / "b"
    write(d, make(("Глава", 90), ("Литература", 10)), {"duration": 50.0, "words": [[0, 0, 1, 1.0, 40.25]]})
    stamp(d)
    first = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    assert (first["text_end"], first["audio_end"]) == (90, 40.25)
    stamp(d)
    again = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    assert again == first


def test_a_book_without_audio_has_no_audio_end(tmp_path):
    d = tmp_path / "b"
    write(d, make(("Глава", 90)), {"duration": 50.0, "words": []})
    stamp(d)
    (d / "timing.json").unlink()
    stamp(d)
    meta = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    assert meta["text_end"] == 90 and "audio_end" not in meta
