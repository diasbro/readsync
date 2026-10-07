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


# ---------------------------------------------------------------- the text layer (pipeline/extract_pdf.py)

L = pdf.Line


def body(text: str, y: float, x: float = 56.0, x1: float = 350.0, size: float = 12.0, bold: bool = False):
    return L(text, x, x1, y, size, bold)


def test_glyph_names_win_over_a_tounicode_map_that_contradicts_them():
    """F32: InDesign wrote /uni0411 (Б) with a ToUnicode entry for б; the glyph name is the truth."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    tu = DecodedStreamObject()
    tu.set_data(b"begincmap\n2 beginbfchar\n<01> <0431>\n<02> <0443>\nendbfchar\nendcmap\n")
    enc = DictionaryObject({NameObject("/Differences"): ArrayObject([NumberObject(1), NameObject("/uni0411")])})
    fd = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/MinionPro-Bold"),
            NameObject("/Encoding"): enc,
            NameObject("/ToUnicode"): tu,
            NameObject("/FirstChar"): NumberObject(1),
            NameObject("/Widths"): ArrayObject([NumberObject(580), NumberObject(500)]),
        }
    )
    f = pdf.Font(fd)
    assert [t for _, t, _ in f.codes(b"\x01\x02")] == ["Б", "у"]
    assert f.codes(b"\x01")[0][2] == 0.58
    assert f.bold


def test_a_capital_set_apart_by_the_typesetter_stays_in_its_word():
    """F32: a capital drawn on its own and the rest of the word moved next to it are one word; a word gap is a
    space."""
    g = pdf.Glyph
    glyphs = [g("Б", 56.0, 63.0, 500.0, 12.0, False)]
    x = 63.0
    for c in "удда":
        glyphs.append(g(c, x, x + 6.0, 500.0, 12.0, False))
        x += 6.0
    glyphs.append(g("и", x + 3.0, x + 9.0, 500.0, 12.0, False))  # a word gap of 3 pt
    glyphs.append(g("*", x + 9.2, x + 12.0, 504.0, 7.0, False))  # a raised marker stays on the line
    (line,) = pdf.glyph_lines(glyphs)
    assert line.text == "Будда и*"
    assert line.size == 12.0


def test_a_word_broken_for_the_line_is_joined():
    """F33: `подчи-` + `нённый`, also with U+2010 or a soft hyphen at the line end."""
    v = pdf.vocabulary([])
    assert pdf.join("означает подчи-", "нённый, или", v) == "означает подчинённый, или"
    assert pdf.join("подчи‐", "нённый", v) == "подчинённый"
    assert pdf.join("подчи­", "нённый", v) == "подчинённый"
    assert pdf.join("и вот -", "дальше", v) == "и вот - дальше"  # a dash after a space keeps its spaces


def test_a_real_hyphen_at_a_line_end_stays():
    """F34: particles, кое-, a word the book spells with a hyphen elsewhere, a capital after the hyphen."""
    v = pdf.vocabulary([[body("Он сказал это по-русски, и все поняли.", 0)]])
    assert pdf.join("чем-", "то материальным", v) == "чем-то материальным"
    assert pdf.join("кто-", "либо", v) == "кто-либо"
    assert pdf.join("кое-", "как", v) == "кое-как"
    assert pdf.join("сказал по-", "русски", v) == "сказал по-русски"
    assert pdf.join("Нью-", "Йорк", v) == "Нью-Йорк"
    assert pdf.join("по-", "мощь", v) == "помощь"  # spelled neither way: a word broken for the line
    # the word itself as the book spells it away from line ends decides
    v2 = pdf.vocabulary([[body("Аджан пришёл.", 0)]])
    assert pdf.join("Ад-", "жан", v2) == "Аджан"


def test_unicode_hyphens_become_plain():
    """F39."""
    assert pdf.finish("нео‐буддизм и чем‑то,  со­всем") == "нео-буддизм и чем-то, совсем"


def test_footnotes_at_the_foot_become_notes_linked_at_their_marker():
    """F35: smaller lines after a gap at a page's foot, each starting with its marker; the text above runs on
    over the page break without them, and a foot that goes on the next page continues its note."""
    page1 = [
        body("Начало главы о страхе перед страданием (дуккха)*, которое не", 500, x=70),
        body("способна ослабить никакая сила, и здесь о подношениях** тоже", 487),
        body("сказано, что они не", 474),
        body("* Слова страдание и неудовлетворительность будут", 440, x=60, x1=330, size=9.0),
        body("использоваться для перевода дуккха.", 430, x=60, x1=200, size=9.0),
        body("** В буддийской традиции ценится щедрость, и", 420, x=60, x1=330, size=9.0),
    ]
    page2 = [
        body("помогают. Конец абзаца.", 500, x1=150),
        body("Новый абзац начинается здесь и идёт до самого правого края", 487, x=70),
        body("и кончается.", 474, x1=120),
        body("заслуги приносят счастье.", 440, x=60, x1=200, size=9.0),
    ]
    book = pdf.book_from_lines([page1, page2], "Книга", "")
    first = book["blocks"][0]
    assert first["text"].startswith("Начало главы о страхе перед страданием (дуккха), которое не способна")
    assert "о подношениях тоже сказано, что они не помогают. Конец абзаца." in first["text"]
    assert [(n["id"], n["m"]) for n in first["notes"]] == [("n1", "*"), ("n2", "**")]
    assert first["text"][: first["notes"][0]["pos"]].endswith("(дуккха)")
    assert first["text"][: first["notes"][1]["pos"]].endswith("о подношениях")
    assert book["notes"] == {
        "n1": "Слова страдание и неудовлетворительность будут использоваться для перевода дуккха.",
        "n2": "В буддийской традиции ценится щедрость, и заслуги приносят счастье.",
    }
    assert book["blocks"][1]["text"].startswith("Новый абзац")


def test_headings_by_their_look_not_by_a_pattern():
    """F36: a larger line between paragraphs is a chapter, a bold one in the text's size a subtitle (a chapter
    when centred or in capitals); a short line in capitals in the text's own face is text."""
    page = [
        body("ВЗГЛЯД НА БУДДИЗМ", 560, x=140, x1=260, size=14.0),
        body("Первая строка абзаца, которая тянется до самого правого края", 530, x=70),
        body("колонки и кончается тут.", 517, x1=200),
        body("THAILAND", 504, x=70, x1=120),
        body("Он был уникален.", 478, x=70, x1=170, bold=True),
        body("Буддадаса помнит всё, что написано в книгах, и рассказывает", 452, x=70),
        body("об этом.", 439, x1=100),
        body("ИСТИННАЯ ПРИРОДА ВЕЩЕЙ", 413, x=120, x1=280, bold=True),
        body("Слово религия имеет более широкий смысл, чем слово нравственность,", 387, x=70),
        body("и шире.", 374, x1=100),
    ]
    book = pdf.book_from_lines([page], "Книга", "")
    assert [(c["title"], c["level"]) for c in book["chapters"]] == [
        ("ВЗГЛЯД НА БУДДИЗМ", 1),
        ("ИСТИННАЯ ПРИРОДА ВЕЩЕЙ", 2),
    ]
    kinds = [(b["kind"], b["text"][:20]) for b in book["blocks"]]
    assert ("p", "THAILAND") in kinds
    assert ("subtitle", "Он был уникален.") in kinds


def test_a_table_of_contents_goes():
    """F37: a page of dot-leader entries, its heading too; a stray entry elsewhere."""
    toc = [
        body("СОДЕРЖАНИЕ", 560, x=160, x1=240, bold=True),
        body("Взгляд на буддизм ......................... 13", 500),
        body("Истинная природа вещей ................... 26", 480),
        body("Три универсальные характеристики . . . . . 36", 460),
    ]
    text = [
        body("Глава первая", 560, size=16.0, x1=150),
        body("Текст главы идёт здесь, и он длинный, до самого правого края.", 530, x=70),
        body("Заключение ........ 123", 517),
    ]
    book = pdf.book_from_lines([toc, text], "Книга", "")
    assert [c["title"] for c in book["chapters"]] == ["Глава первая"]
    assert all("....." not in b["text"] and "СОДЕРЖАНИЕ" not in b["text"] for b in book["blocks"])


def test_paragraphs_by_indent_and_by_gap():
    """F38: a first-line indent starts a paragraph, so does a wider gap; a one-line paragraph stands alone; lines
    indented alike (a quotation) stay together."""
    page = [
        body("Первый абзац начинается с отступа и идёт до правого края", 600, x=70),
        body("колонки, а кончается здесь.", 587, x1=200),
        body("— Да.", 574, x=70, x1=100),
        body("— Нет, — сказал он, и это был второй абзац, тоже до края", 561, x=70),
        body("колонки.", 548, x1=100),
        body("Абзац после пропуска, без отступа, длинный до правого края", 522),
        body("колонки.", 509, x1=100),
        body("Цитата набрана с отступом слева и справа, и её строки идут", 496, x=80, x1=320),
        body("одна за другой с тем же отступом, пока цитата не кончится.", 483, x=80, x1=320),
    ]
    paras = pdf.join_lines([page]).split("\n\n")
    assert paras == [
        "Первый абзац начинается с отступа и идёт до правого края колонки, а кончается здесь.",
        "— Да.",
        "— Нет, — сказал он, и это был второй абзац, тоже до края колонки.",
        "Абзац после пропуска, без отступа, длинный до правого края колонки.",
        "Цитата набрана с отступом слева и справа, и её строки идут одна за другой с тем же отступом, пока "
        "цитата не кончится.",
    ]


def test_a_typeset_pdf_end_to_end():
    """The shared vector tests/import_vectors/lekcii.pdf through the whole extractor: no contents page, no running
    head or page numbers, chapters by size, notes, hyphenation."""
    book = pdf.extract(Path(__file__).resolve().parent / "import_vectors" / "lekcii.pdf")
    text = "\n".join(b["text"] for b in book["blocks"])
    assert [c["title"] for c in book["chapters"]] == ["Лекция первая", "Лекция вторая"]
    assert "Содержание" not in text and ". . ." not in text and "ЛЕКЦИИ О МАЯКАХ" not in text
    assert "что-то шумело" in text and "переносом через залив" in text
    assert book["notes"]["n1"].endswith("кроме одной.")
    assert "маяк, и его огонь" in text


# ---------------------------------------------------------------- page numbers, notes and contents (pdf.json)


def _case(name: str) -> dict:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_import_vectors", Path(__file__).resolve().parent / "make_import_vectors.py"
    )
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    pages = dict(m.pdf_line_cases())[name]
    return pdf.book_from_lines([[L(*ln) for ln in pg] for pg in pages], name, "")


def test_page_numbers_count_on_and_chapter_numerals_stay():
    """X38: lone numbers at the foot that count on with the pages go; II and III on pages two apart, IV in a larger
    font and a word like civil stay; X45: a line ending «… 1945» is text, and a page of contents late in the book
    loses only its entries."""
    book = _case("folios")
    assert [c["title"] for c in book["chapters"]] == ["", "II", "III", "IV"]
    text = [b["text"] for b in book["blocks"]]
    assert "civil" in text
    assert not any(t in {"7", "8", "9", "10", "11"} for t in text)
    assert any(t.endswith("как ждут весны… 1945") for t in text)
    assert not any("........" in t for t in text) and text[-1].startswith("Ветер шумел")
    assert pdf.page_number("xiv") == 14 and pdf.page_number("- 12 -") == 12 and pdf.page_number("стр. 5") == 5
    assert pdf.page_number("civil") is None and pdf.page_number("mild") is None and pdf.page_number("dim") is None


def test_numbers_glued_to_capitals_or_abbreviations_are_not_note_markers():
    """X39: CO2, т.1, гл.2 are not markers, слово1 is; X40: a note whose marker the text does not show is linked
    where the page's text ends; a foot line out of the notes' order goes on with the last note; X41: a private-use
    character of the text stays; X46: `* * *` at a page's foot is no note."""
    book = _case("notes")
    blocks = book["blocks"]
    b = next(x for x in blocks if "CO2" in x["text"])
    assert "Вода и CO2 в воздухе, см. т.1 и гл.2, а слово стоит тут" in b["text"]
    assert b["notes"] == [{"pos": b["text"].index(" стоит"), "id": "n1", "m": "1"}]
    assert "знак \ue000 тоже" in b["text"]
    after = blocks[blocks.index(b) + 1]
    assert after["notes"] == [{"pos": len(after["text"]), "id": "n2", "m": "2"}]
    assert book["notes"]["n2"] == "Вторая сноска, метки которой нет в тексте, и она идёт 12 апреля всё кончилось."
    assert any(x["text"] == "* * *" for x in blocks)
    assert book["notes"]["n3"] == "Сноска со звездой."


def test_note_sentinels_skip_what_the_text_uses():
    """X41: the sentinels are code points the book's own text does not hold."""
    page = [
        body("Слово\U000f0000 и знак1 тут, и строка тянется до самого правого края колонки.", 500),
        body("1 Сноска.", 440, size=9.0),
    ]
    book = pdf.book_from_lines([page, [body("Ещё текст.", 500)]], "Книга", "")
    first = book["blocks"][0]
    assert first["text"].startswith("Слово\U000f0000 и знак тут")
    assert first["notes"] == [{"pos": first["text"].index(" тут"), "id": "n1", "m": "1"}]


def test_a_bfrange_past_two_bytes_drops_only_itself():
    """X43."""
    cmap = "beginbfchar <0001> <0041> endbfchar beginbfrange <0010> <0020> <FFF8> <0030> <0031> <0430> endbfrange"
    assert pdf.parse_tounicode(cmap) == {1: "A", 0x30: "а", 0x31: "б"}


def test_fonts_are_read_once_per_document(monkeypatch):
    """X42: a font used on every page is parsed once."""
    made = []
    real = pdf.Font.__init__

    def counting(self, fd):
        made.append(1)
        real(self, fd)

    monkeypatch.setattr(pdf.Font, "__init__", counting)
    reader = pdf.open_pdf(Path(__file__).resolve().parent / "import_vectors" / "lekcii.pdf")
    pages = pdf.page_lines(reader)
    assert len(pages) > 2 and len(made) == 1


def test_a_title_pypdf_leaves_as_bytes_is_read():
    """X47."""
    assert pdf._meta_text("Пушкин".encode("cp1251") + b"\x98").startswith("Пушкин")
    assert pdf._meta_text(b"\xfe\xff" + "Т".encode("utf-16-be")) == "Т"
    assert pdf._meta_text(None) == ""
