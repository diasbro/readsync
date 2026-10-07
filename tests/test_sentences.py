"""The sentence splitter (extract_text.split_sentences) on the cases the 2026-10 extraction audit found, and the
canonical book.json writer (dump_book). The phone's port is held to the same cases by
tests/import_vectors/sentences.json and model.book.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from extract_text import block_sentences, dump_book, split_sentences  # noqa: E402


def sents(text: str) -> list[str]:
    return [text[a:b] for a, b in split_sentences(text)]


# F19: an initial does not end a sentence


def test_initial_before_a_surname():
    assert sents("Это был лишь повод Н. Жирардо написать книгу. Он её написал.") == [
        "Это был лишь повод Н. Жирардо написать книгу.",
        "Он её написал.",
    ]


def test_initials_in_a_copyright_line():
    assert sents("© Пелевин В. О., текст, 2017") == ["© Пелевин В. О., текст, 2017"]


def test_chained_initials():
    assert sents("Перевод Ю.Щ. Шуцкого. Издание второе.") == ["Перевод Ю.Щ. Шуцкого.", "Издание второе."]
    assert sents("Редактор В. О. Пелевин. Корректор А. Б. Ветров.") == [
        "Редактор В. О. Пелевин.",
        "Корректор А. Б. Ветров.",
    ]
    assert sents("Translated by J. R. Smith. Second edition.") == ["Translated by J. R. Smith.", "Second edition."]


# F20: scholarly abbreviations


def test_page_and_chapter_references():
    assert sents("Об этом сказано выше (гл. 7). Далее.") == ["Об этом сказано выше (гл. 7).", "Далее."]
    assert sents("Подробнее (см. с. 135) у автора. Дальше.") == ["Подробнее (см. с. 135) у автора.", "Дальше."]


def test_century_and_languages():
    assert sents("Жил в III в. до н. э., его не знали.") == ["Жил в III в. до н. э., его не знали."]
    assert sents("Слово «дао» (кит. 道) значит путь. Второе.") == ["Слово «дао» (кит. 道) значит путь.", "Второе."]
    assert sents("Это т. н. великий предел, ср. Беспредельное. Конец.") == [
        "Это т. н. великий предел, ср. Беспредельное.",
        "Конец.",
    ]
    assert sents("Он изд. в 1900, ок. 1900, акад. Иванов, англ. mind, нем. «Geist», лат. (homo). Всё.") == [
        "Он изд. в 1900, ок. 1900, акад. Иванов, англ. mind, нем. «Geist», лат. (homo).",
        "Всё.",
    ]


def test_english_abbreviations():
    assert sents("See e.g. the book, i.e. the man Mr. Smith and Dr. Watson, vol. 2, p. 15, pp. 3-4, ch. 5. Next.") == [
        "See e.g. the book, i.e. the man Mr. Smith and Dr. Watson, vol. 2, p. 15, pp. 3-4, ch. 5.",
        "Next.",
    ]


# an abbreviation that is also a word, or one letter, ends a sentence before a capital


def test_word_like_abbreviations_end_before_a_capital():
    assert sents("Под нами проплыл кит. Он плыл на север.") == ["Под нами проплыл кит.", "Он плыл на север."]
    assert sents("Кит. Он плыл.") == ["Кит.", "Он плыл."]
    assert sents("Жил в III в. До него не знали.") == ["Жил в III в.", "До него не знали."]
    assert sents("Это т. е. Путь. Конец.") == ["Это т. е.", "Путь.", "Конец."]
    assert sents("Я дал им. Они ушли.") == ["Я дал им.", "Они ушли."]


def test_word_like_abbreviations_stay_before_anything_else():
    assert sents("Слово (кит. 天) значит небо.") == ["Слово (кит. 天) значит небо."]
    assert sents("См. с. 135 и гл. 7, ст. 3. Конец.") == ["См. с. 135 и гл. 7, ст. 3.", "Конец."]
    assert sents("Это рис. (вверху) и пер. с англ. «Mind». Всё.") == [
        "Это рис. (вверху) и пер. с англ. «Mind».",
        "Всё.",
    ]
    assert sents("Около 5 ч. утра.") == ["Около 5 ч. утра."]


def test_initials_stay_before_a_capital_even_when_a_letter_is_an_abbreviation():
    assert sents("Автор В. С. Петров. Дальше.") == ["Автор В. С. Петров.", "Дальше."]


# F21: "и т. д.", "и т. п.", "и др." end a sentence before a capital; the others never do


def test_final_abbreviations_end_before_a_capital():
    assert sents("Он любил травы, камни и т. д. Если спросить, молчал.") == [
        "Он любил травы, камни и т. д.",
        "Если спросить, молчал.",
    ]
    assert sents("Реки, горы и т. п. Всё это.") == ["Реки, горы и т. п.", "Всё это."]
    assert sents("Лао-цзы, Чжуан-цзы и др. Они учили.") == ["Лао-цзы, Чжуан-цзы и др.", "Они учили."]
    assert sents("Книги, свитки и т.д. Потом.") == ["Книги, свитки и т.д.", "Потом."]


def test_final_abbreviations_go_on_before_lowercase():
    assert sents("Травы, камни и т. д. всё росло.") == ["Травы, камни и т. д. всё росло."]


def test_other_abbreviations_never_end():
    assert sents("Это т. е. путь. Конец.") == ["Это т. е. путь.", "Конец."]
    assert sents("См. Пятую главу, ср. Шестую. Конец.") == ["См. Пятую главу, ср. Шестую.", "Конец."]
    assert sents("Он и др. учёные пришли. Конец.") == ["Он и др. учёные пришли.", "Конец."]
    assert sents("Улица ул. Ленина, проф. Иванов, св. Пётр. Конец.") == [
        "Улица ул. Ленина, проф. Иванов, св. Пётр.",
        "Конец.",
    ]


# F22: a list number opening a block is not a sentence of its own


def test_list_numbers_join_the_next_sentence():
    assert sents("1. Даос в буддизме. Второе.") == ["1. Даос в буддизме.", "Второе."]
    assert sents("12. Пункт двенадцатый.") == ["12. Пункт двенадцатый."]
    assert sents("IV. Четвёртый раздел. Текст.") == ["IV. Четвёртый раздел.", "Текст."]
    assert sents("а) первое, б) второе.") == ["а) первое, б) второе."]
    assert sents("   1. Отступ стихом.") == ["1. Отступ стихом."]


def test_a_number_inside_a_block_still_ends_a_sentence():
    assert sents("Это было в 1999. Потом всё.") == ["Это было в 1999.", "Потом всё."]


# X32: a single capital that answers ends a sentence; an initial goes before a surname of its script on its line


def test_a_one_letter_answer_ends_a_sentence():
    assert sents("— Кто там? — Я. Ну открывай же.") == ["— Кто там?", "— Я.", "Ну открывай же."]
    assert sents("Кто там? Я. Ну и что.") == ["Кто там?", "Я.", "Ну и что."]
    assert sents("«Кто там?» — Я. Ну.") == ["«Кто там?»", "— Я.", "Ну."]
    assert sents("Сказал он: — Я. Нет.") == ["Сказал он: — Я.", "Нет."]
    assert sents("— Я. А ты?") == ["— Я.", "А ты?"]


def test_a_letter_in_another_script_ends_a_sentence():
    assert sents("Витамин C. Его много.") == ["Витамин C.", "Его много."]
    assert sents("Translated by J. Smith. Next.") == ["Translated by J. Smith.", "Next."]
    assert sents("1. A. Первый пункт.") == ["1. A. Первый пункт."]  # a label, not after a word


def test_an_initial_stays_with_its_surname_on_the_same_line():
    assert sents("— А. Б. Петров пришёл.") == ["— А. Б. Петров пришёл."]
    assert sents("Автор Н. Жирардо.") == ["Автор Н. Жирардо."]
    assert sents("Перевод Н.\nЖирардо.") == ["Перевод Н.", "Жирардо."]


def test_numbers_and_others_end_before_a_capital():
    assert sents("5 млн. Это много.") == ["5 млн.", "Это много."]
    assert sents("3 млрд. рублей, тыс. Дальше, стр. 5.") == ["3 млрд. рублей, тыс.", "Дальше, стр. 5."]
    assert sents("Он и пр. учёные. Конец.") == ["Он и пр. учёные.", "Конец."]


def test_weak_abbreviations_stay_before_another_script():
    assert sents("Слово от англ. Love, нем. Geist. Это т. е. Путь.") == [
        "Слово от англ. Love, нем. Geist.",
        "Это т. е.",
        "Путь.",
    ]


def test_a_straight_single_quote_opens_a_sentence():
    assert sents("Он ушёл. 'Привет', сказал я.") == ["Он ушёл.", "'Привет', сказал я."]


def test_line_break_is_a_space():
    assert sents("Первая строка.\nВторая строка.") == ["Первая строка.", "Вторая строка."]


def test_non_heading_kinds_are_split():
    text = "Первое. Второе."
    for kind in ("p", "cite", "epigraph", "verse", "annotation", "author"):
        assert block_sentences(text, kind) == [[0, 7], [8, 15]], kind
    for kind in ("title", "subtitle"):
        assert block_sentences(text, kind) == [[0, 15]], kind


# dump_book


def test_dump_book_orders_and_drops_empty_keys():
    block = {
        "st": {"a": "c"},
        "rows": [[[0, 3, 1], [4, 7]]],
        "pics": [],
        "audio": False,
        "sentences": [[0, 7]],
        "notes": [{"m": "1", "id": "n1", "pos": 3}, {"pos": 5, "id": "n2", "m": ""}],
        "sub": [],
        "sup": [[5, 6]],
        "strong": [[0, 3]],
        "em": [],
        "text": "Кит\tсоль",
        "stanza": None,
        "chapter": 0,
        "kind": "table",
        "id": "b0",
        "images": [{"after": True, "h": 2, "w": 1, "src": "images/a.png"}, {"src": "images/b.png", "after": False}],
    }
    book = {
        "title": "Т",
        "author": "А",
        "chapters": [{"id": "s0", "title": "", "level": 1, "first_block": 0}],
        "blocks": [block],
        "notes": {
            "n1": "Просто.",
            "n2": {"text": "Две\n\nстроки"},
            "n3": {"kinds": [[0, 3, "verse"]], "pics": [], "em": [[0, 1]], "text": "Стих"},
            "n4": {"text": "Один", "em": []},
        },
    }
    assert dump_book(book) == (
        '{"title": "Т", "author": "А", "chapters": [{"id": "s0", "title": "", "level": 1, "first_block": 0}], '
        '"blocks": [{"images": [{"src": "images/a.png", "w": 1, "h": 2, "after": true}, {"src": "images/b.png"}], '
        '"id": "b0", "kind": "table", "chapter": 0, "stanza": null, "text": "Кит\\tсоль", "em": [], '
        '"strong": [[0, 3]], "sup": [[5, 6]], "notes": [{"pos": 3, "id": "n1", "m": "1"}, {"pos": 5, "id": "n2"}], '
        '"sentences": [[0, 7]], "audio": false, "rows": [[[0, 3, 1], [4, 7]]], "st": {"a": "c"}}], '
        '"notes": {"n1": "Просто.", "n2": {"text": "Две\\n\\nстроки"}, '
        '"n3": {"text": "Стих", "em": [[0, 1]], "kinds": [[0, 3, "verse"]]}, "n4": "Один"}}'
    )


def test_dump_book_is_json_dumps_for_todays_books():
    book = {
        "title": "Т",
        "author": "",
        "chapters": [],
        "blocks": [
            {
                "images": [{"src": "images/a.png", "w": 3, "h": 4}],
                "id": "b0",
                "kind": "p",
                "chapter": 0,
                "stanza": None,
                "text": "Кит.",
                "em": [[0, 3]],
                "notes": [{"pos": 3, "id": "n1"}],
                "sentences": [[0, 4]],
                "audio": True,
                "pics": [{"pos": 1, "src": "images/b.png"}],
                "st": {"i": 2},
            }
        ],
        "notes": {"n1": "Примечание."},
    }
    assert dump_book(book) == json.dumps(book, ensure_ascii=False)
