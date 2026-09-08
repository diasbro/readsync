"""PDF text: page furniture, hyphenation and where a paragraph ends. No PDF reader needed."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import extract_pdf as pdf  # noqa: E402

PAGES = [
    "ЛАО-ЦЗЫ. КНИГА ПУТИ\nПервая строка первой страницы, довольно длинная, как в книге.\nвторая строка, тоже длинная, чтобы ширина колонки была видна.\nи третья.\n12",
    "ЛАО-ЦЗЫ. КНИГА ПУТИ\nСтрока с перено-\nсом слова внутри абзаца, которая тянется до края колонки тут.\nКонец абзаца.\n13",
]


def test_running_head_and_page_number_go_away():
    pages = pdf.strip_furniture(PAGES * 2)  # four pages: the head repeats on all of them
    flat = [ln for page in pages for ln in page]
    assert not any("КНИГА ПУТИ" in ln for ln in flat)
    assert not any(ln.strip() in {"12", "13"} for ln in flat)


def test_hyphenated_word_is_put_back_together():
    text = pdf.join_lines(pdf.strip_furniture(PAGES * 2))
    assert "переносом слова" in text
    assert "перено- сом" not in text


def test_a_short_line_that_ends_a_sentence_ends_the_paragraph():
    text = pdf.join_lines(pdf.strip_furniture(PAGES * 2))
    paras = [p for p in text.split("\n\n") if p]
    assert paras[0].endswith("и третья.")
    assert paras[1].startswith("Строка с переносом")


def test_a_heading_stands_alone():
    text = pdf.join_lines([["Глава первая", "Ветер поднимался над рекой, и лодка отходила от берега медленно."]])
    assert text.split("\n\n")[0] == "Глава первая"


def test_pages_of_only_furniture_do_not_break():
    """A page whose every line is a running head or a number empties out, it does not raise."""
    assert pdf.strip_furniture(["12", "13", "14", "15"]) == [[], [], [], []]
    assert pdf.strip_furniture([]) == []
    assert pdf.join_lines([]) == ""
