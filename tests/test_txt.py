"""Plain-text books (pipeline/extract_txt.py): the encoding, paragraphs, chapters, scene breaks and asterisk notes.
The small texts are tests/make_import_vectors.py's `txt_cases`, which the phone is held to as well."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
import extract_style  # noqa: E402
import extract_txt  # noqa: E402
from extract_txt import decode, russian_score  # noqa: E402

PLAIN = "Глава 1\n\nМама мыла раму, а папа читал газету. Ёжик съел яблоко.\n"


def _vectors():
    spec = importlib.util.spec_from_file_location("make_import_vectors", HERE / "make_import_vectors.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


V = _vectors()
CASES = dict(V.txt_cases())


def book(name: str) -> dict:
    return V.extract_txt_bytes(name, CASES[name])


def texts(b: dict) -> list[tuple[str, str]]:
    return [(blk["kind"], blk["text"]) for blk in b["blocks"]]


# ---------------------------------------------------------------- encoding


@pytest.mark.parametrize(
    "name", ["utf8", "utf8-bom", "utf16le-bom", "utf16be-bom", "utf32le-bom", "utf32be-bom", "cp1251", "koi8-r",
             "cp866", "mac-cyrillic"],
)  # fmt: skip
def test_every_encoding_reads_the_same_text(name):
    assert decode(CASES[name]) == PLAIN


def test_a_byte_order_mark_decides_and_broken_bytes_are_replaced():
    assert decode(b"\xff\xfe\x3f\x04\x41") == "п�"
    assert decode(b"\xef\xbb\xbf\xd0\xbf\xff") == "п�"
    assert decode(b"\xfe\xff\x00A") == "A"


def test_the_code_page_is_the_one_that_reads_as_russian_not_the_first_that_decodes():
    raw = PLAIN.encode("koi8_r")
    raw.decode("cp1251")  # decodes without an error, as nonsense
    assert decode(raw) == PLAIN
    scores = {enc: russian_score(raw.decode(enc, "replace")) for enc in extract_txt.CYRILLIC}
    assert max(scores, key=scores.get) == "koi8_r"


@pytest.mark.parametrize("enc", extract_txt.CYRILLIC)
@pytest.mark.parametrize(
    "text",
    [
        " ".join(V.prose(3, 12)).replace("«", '"').replace("»", '"').replace("—", "-").replace("…", "..."),
        "Привет",
        "ВСЁ НАПИСАНО ЗАГЛАВНЫМИ БУКВАМИ, КАК ТЕЛЕГРАММА.",
        '- Да, - сказал он. - Нет! "Почему?" - спросила Анна... - Потому.',
        "Ёлка. Ещё. Её. Объём. Съёмка. ЁЖ.",
    ],
)
def test_each_code_page_wins_on_its_own_bytes(enc, text):
    raw = text.encode(enc)
    assert decode(raw) == text


def test_all_capitals_in_koi8_are_still_read_as_koi8():
    assert decode(CASES["caps-koi8-r"]).startswith("ГЛАВА 1\n\nВСЁ НАПИСАНО")


def test_a_text_that_is_not_russian_is_latin_1():
    assert decode(CASES["latin-1"]) == "Café au lait, déjà vu.\n\nII\n\nÜber München.\n"
    assert decode("Plain © 2020 text".encode("latin-1")) == "Plain © 2020 text"
    assert decode("Señor García está aquí.".encode("latin-1")) == "Señor García está aquí."


def test_diaries_in_other_code_pages_are_the_cp1251_one():
    src = {n: (d, o) for n, d, o in V.sources()}
    twin = V.extract_txt_bytes("x", src["dnevnik-1251.txt"][0])
    twins = [n for n, (_, o) in src.items() if o.get("same_as") == "dnevnik-1251.txt"]
    assert sorted(twins) == ["dnevnik-866.txt", "dnevnik-koi8.txt"]
    for n in twins:
        assert V.extract_txt_bytes("x", src[n][0]) == twin, n


def test_the_phone_has_the_same_code_pages():
    swift = (HERE.parent / "ios" / "Sources" / "Import" / "TXT.swift").read_text(encoding="utf-8")
    pages = dict(re.findall(r'\("(\w+)", \[(\s*0x[^\]]*)\]\)', swift))
    assert list(pages) == list(extract_txt.CYRILLIC)
    for enc, body in pages.items():
        want = [ord(c) for c in bytes(range(128, 256)).decode(enc, "replace")]
        assert [int(x, 16) for x in re.findall(r"0x[0-9A-F]+", body)] == want, enc


# ---------------------------------------------------------------- chapters


def test_headings_that_stand_alone():
    b = book("headings")
    assert [c["title"] for c in b["chapters"]] == [
        "Часть вторая", "Глава I", "CHAPTER TWELVE", "IV.", "12", "КОНЕЦ ПУТИ", "Пролог", "Книга третья",
        "Глава 7. Встреча",
    ]  # fmt: skip
    assert [c["level"] for c in b["chapters"]] == [1, 2, 2, 2, 2, 2, 2, 1, 2]
    assert all(len(blk["sentences"]) == 1 for blk in b["blocks"] if blk["kind"] == "title")


def test_capitals_dialogue_lists_and_lines_with_text_under_them_are_not_headings():
    b = book("not-headings")
    assert all(blk["kind"] == "p" for blk in b["blocks"])
    assert [blk["text"] for blk in b["blocks"]][1:3] == ["ЗИМА!", "Пришла зима."]


def test_a_strong_heading_on_the_first_line_of_a_paragraph_is_split_off():
    b = book("first-line-heading")
    assert texts(b) == [
        ("p", "Текст до главы."), ("title", "Глава 3"), ("p", "Текст главы сразу под заголовком."),
        ("title", "ЗИМА"), ("p", "Пришла зима."), ("p", "Конец."),
    ]  # fmt: skip


def test_a_text_all_in_capitals_has_no_capital_headings():
    b = book("caps-koi8-r")
    assert [c["title"] for c in b["chapters"]] == ["ГЛАВА 1"]
    assert texts(b)[2] == ("p", "КОНЕЦ")


def test_capitals_and_numbers_inside_a_wrapped_paragraph_are_text():
    src = {n: d for n, d, _ in V.sources()}
    b = V.extract_txt_bytes("zapiski", src["zapiski.txt"])
    assert [c["title"] for c in b["chapters"]] == ["Часть первая", "Глава 1", "II", "ГЛАВА ТРЕТЬЯ"]
    assert any(blk["text"].endswith("и река ждал весь вечер!") for blk in b["blocks"])
    para = next(blk["text"] for blk in b["blocks"] if "ВХОД" in blk["text"])
    assert "с воды: ВХОД ВОСПРЕЩЁН и ещё число" in para and "в 12 часов ночи" in para
    assert ("p", "— СТОЙ!") in texts(b)


def test_a_chapter_heading_on_the_last_line_of_a_paragraph_is_split_off_but_not_capitals():
    t = texts(book("last-line-heading"))
    assert [k for k, _ in t] == ["p", "title", "p", "p"]
    assert t[0][1].endswith("где спали рыбаки.") and t[1][1] == "Глава 3"
    assert t[2][1].endswith("над водой залива. ЗИМА")


# ---------------------------------------------------------------- paragraphs


def test_hard_wrapped_text_splits_at_indents_and_short_lines_and_joins_hyphenated_words():
    b = book("hard-wrapped")
    assert [c["title"] for c in b["chapters"]] == ["Глава 1"]
    t = texts(b)
    assert [k for k, _ in t] == ["title", "p", "p", "p"]
    assert t[1][1].startswith("Маяк стоял") and "переносом через залив" in t[1][1]
    assert t[1][1].endswith("рыбаки, и так было всегда.")
    assert t[2][1].startswith("Сторож") and t[2][1].endswith("и рыбаки на берегу.")
    assert t[3][1].startswith("Так было всегда, сколько")


def test_a_wrapped_text_without_blank_lines_has_centred_headings():
    src = {n: d for n, d, _ in V.sources()}
    b = V.extract_txt_bytes("rukopis", src["rukopis.txt"])
    assert [c["title"] for c in b["chapters"]] == ["ГЛАВА 1", "Глава 2"]
    assert ("p", "— СТОЙ! — крикнул сторож с берега.") in texts(b)
    assert b["notes"] == {"n1": "Так в рукописи."}


def test_one_line_per_paragraph():
    b = book("crlf")
    assert texts(b) == [("title", "Глава 1"), ("p", "Текст."), ("p", "Ещё."), ("p", "И ещё.")]


def test_nothing_to_read():
    assert book("empty")["blocks"] == []


# ---------------------------------------------------------------- scene breaks and notes


def test_scene_breaks_are_gaps_not_text():
    b = book("breaks")
    assert [(blk["text"], blk.get("st")) for blk in b["blocks"]] == [
        ("Раз.", None), ("Два.", {"g": 1}), ("Три.", {"g": 1}), ("Четыре.", {"g": 2}), ("Глава 2", None),
        ("Пять.", None),
    ]  # fmt: skip


def test_scene_breaks_without_styles(monkeypatch):
    monkeypatch.setattr(extract_style, "ENABLED", False)
    assert not any("st" in blk for blk in book("breaks")["blocks"])


def test_asterisk_footnotes_become_notes():
    b = book("notes")
    assert texts(b) == [
        ("p", "Это МЕХАНИЗМ мира, и ВЕЩЬ тоже."), ("p", "Там «слово»."), ("p", "*Важно* сказать."),
        ("p", "Ещё абзац."),
    ]  # fmt: skip
    assert b["blocks"][0]["notes"] == [{"pos": 12, "id": "n1", "m": "*"}, {"pos": 25, "id": "n2", "m": "**"}]
    assert b["blocks"][1]["notes"] == [{"pos": 11, "id": "n3", "m": "*"}]
    assert b["notes"] == {"n1": "Устройство (нем.).", "n2": "Вторая сноска.", "n3": "Третья сноска."}
    assert [s for s in b["blocks"][0]["sentences"]] == [[0, 31]]


def test_a_paragraph_starting_with_an_asterisk_without_a_marker_is_text():
    src = {n: d for n, d, _ in V.sources()}
    b = V.extract_txt_bytes("zametki", src["zametki.txt"])
    assert ("p", "*Важно* сказать: звезда* здесь не сноска.") in texts(b)
    assert b["notes"] == {"n1": "Первая сноска.", "n2": "Вторая сноска."}
    assert [c["title"] for c in b["chapters"]] == ["", "ЗИМА", "3", "Глава четвёртая", "Эпилог"]
    assert sum(1 for blk in b["blocks"] if blk.get("st") == {"g": 1}) == 2


def test_build_takes_paragraphs_made_elsewhere():
    b = extract_txt.build(["Глава 1", "Текст главы, обычный.", "— СТОЙ!"], "Книга")
    assert texts(b) == [("title", "Глава 1"), ("p", "Текст главы, обычный."), ("p", "— СТОЙ!")]


def test_a_note_body_comes_within_three_paragraphs_and_a_word_in_asterisks_is_emphasis():
    """X34: the closing `*` of `*важное*` is no marker, so a later `*хлеб` stays text; a body four paragraphs after
    its marker is text too, one right after it a note."""
    b = book("notes-near")
    t = texts(b)
    assert ("p", "Это *важное* слово и хлеб.") in t and ("p", "*хлеб с маслом") in t
    assert ("p", "Слово* тут.") in t and ("p", "* Далёкая сноска.") in t
    assert ("p", "Слово здесь.") in t
    assert b["notes"] == {"n1": "Близкая сноска."}
    assert extract_txt.note_marks("*Важно* сказать: звезда* тут, (слово)* и 5*3.") == [(23, 1), (37, 1)]


def test_a_utf8_text_with_a_few_broken_bytes_stays_utf8():
    """X35: a stray byte and a cut sequence are replaced; a text where fewer than 99% of the bytes are UTF-8 is a
    code page's."""
    text = decode(CASES["utf8-stray-byte"])
    assert "раму,\ufffd а" in text and "Мама мыла раму" in text
    raw = b"abc\xe2\x82 \xed\xa0\x80d" + b"." * 1000
    assert decode(raw) == "abc\ufffd \ufffd\ufffd\ufffdd" + "." * 1000  # as Python's "replace" reads it
    assert decode(CASES["cp1251-few-letters"]).endswith("Привет")


def test_utf16_without_a_byte_order_mark():
    """W22: zero bytes on one side of each pair are UTF-16, little- or big-endian."""
    assert decode(CASES["utf16le-no-bom"]) == PLAIN
    assert decode(CASES["utf16be-no-bom"]) == PLAIN
    assert extract_txt.bomless_utf16("Текст без пробелов".replace(" ", "").encode("utf-16-le")) is None
    assert extract_txt.bomless_utf16(bytes(range(256)) * 4) is None  # zeros now and then: not text
    assert extract_txt.bomless_utf16(b"\x00\x00ab" * 100) is None  # zeros on both sides


def test_a_sentence_that_starts_with_epilogue_is_not_a_heading():
    """X36: Пролог/Эпилог/Предисловие/Послесловие alone or with a short rest, not a sentence of five words."""
    b = book("prologue-sentence")
    assert [c["title"] for c in b["chapters"]] == ["", "Эпилог", "Предисловие автора"]
    assert texts(b)[0] == ("p", "Пролог к этой истории написал мой дед.")
    assert extract_txt.heading_line("Эпилог. Через десять лет", True)
    assert extract_txt.heading_line("Послесловие переводчика к этой книге", True)


def test_a_hyphen_at_a_line_end_stays_where_the_word_has_it():
    """X37: что-то, a word the text spells with a hyphen elsewhere; a word broken for the line is joined."""
    (p, _) = [blk["text"] for blk in book("hyphen-words")["blocks"]]
    assert "дул северо-западный ветер" in p and "снова что-то шумело" in p and "та перемена погоды" in p


def test_heading_words_end_where_python_and_the_phone_agree():
    """W21: a stress mark after a heading's number or word does not hide the heading; no \\b in the patterns."""
    b = book("heading-marks")
    assert [c["title"] for c in b["chapters"]] == ["Глава 1\u0301", "Глава первая\u0301", "Глава 1½"]
    assert not any("\\b" in r.pattern for r in (extract_txt.KEYWORD, extract_txt.STRONG, extract_txt.BIG_PART))
