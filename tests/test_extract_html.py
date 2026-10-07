"""HTML extraction (pipeline/extract_text.py): fantasy-worlds reader pages and any other page, on the cases the
2026-10 extraction audit found (F23-F30, and F05/F06 for the space around a removed note link)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from extract_text import dump_book, extract, inline_text  # noqa: E402


def fw(tmp_path: Path, sections: str, notes: str = "") -> dict:
    """A fantasy-worlds reader page with these sections (and these div.note in its notes section)."""
    notes_sec = f'<section class="section notes"><h2 class="notes__title">Примечания</h2>{notes}</section>'
    src = tmp_path / "book.html"
    src.write_text(
        f'<html><head><title>Книга</title></head><body><article id="book-content">{sections}'
        f"{notes_sec if notes else ''}</article></body></html>",
        encoding="utf-8",
    )
    return extract(src)


def note(nid: str, body: str) -> str:
    return (
        f'<div class="note" id="note-{nid}"><div class="note__backrefs"><a class="note__backref" href="#r"></a></div>'
        f'<div class="note__title">1</div><div class="note__body">{body}</div></div>'
    )


def page(tmp_path: Path, body: str) -> dict:
    src = tmp_path / "page.html"
    src.write_text(f"<html><head><title>Страница</title></head><body>{body}</body></html>", encoding="utf-8")
    return extract(src)


def kinds(book: dict) -> list[tuple[str, str]]:
    return [(b["kind"], b["text"]) for b in book["blocks"]]


def test_cite_keeps_its_heading_author_and_blank_lines(tmp_path):  # F23
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><cite><h3>Мема 1</h3><p>Вбойщик!</p>'
        '<div class="empty-line"></div><p>Книги о пути к успеху.</p><p class="text-author">Автор</p></cite></section>',
    )
    assert kinds(book)[1:] == [
        ("subtitle", "Мема 1"),
        ("cite", "Вбойщик!"),
        ("cite", "Книги о пути к успеху."),
        ("author", "Автор"),
    ]
    assert book["blocks"][3]["st"] == {"g": 1}
    assert book["blocks"][1]["sentences"] == [[0, 6]]


def test_poem_and_stanza_titles_are_subtitles(tmp_path):  # F24
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><div class="poem"><h2><p>РОЗА ВЕТРОВСКАЯ</p></h2>'
        '<div class="stanza"><h3>I</h3><p class="verse">Строка раз</p><p class="verse">Строка два</p></div>'
        '<div class="stanza"><p class="verse">Строка три</p></div><p class="text-author">Поэт</p></div></section>',
    )
    assert kinds(book)[1:] == [
        ("subtitle", "РОЗА ВЕТРОВСКАЯ"),
        ("subtitle", "I"),
        ("verse", "Строка раз"),
        ("verse", "Строка два"),
        ("verse", "Строка три"),
        ("author", "Поэт"),
    ]
    assert [b["stanza"] for b in book["blocks"][3:6]] == [1, 1, 2]
    assert [c["title"] for c in book["chapters"]] == ["Глава"]  # a poem's title is no chapter


def test_comment_link_is_a_note_link_with_its_marker(tmp_path):  # F25, F05
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><p>Вот что написано в книге Ицзин <a href="#c_6"><sup>{6}</sup></a>.'
        ' Дальше <a href="#elsewhere">ссылка</a>.</p></section>',
        note("c_6", "<p>Книга перемен.</p>"),
    )
    b = book["blocks"][1]
    assert b["text"] == "Вот что написано в книге Ицзин. Дальше ссылка."
    assert b["notes"] == [{"pos": 30, "id": "c_6", "m": "6"}]
    assert book["notes"] == {"c_6": "Книга перемен."}


def test_note_link_space(tmp_path):  # F05, F06
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2>'
        '<p>Кит <sup class="note"><a data-note-id="n_1" href="#note-n_1">{1}</a></sup>. Конец</p>'
        '<p>в организме<sup class="note"><a data-note-id="n_2" href="#note-n_2">[69]</a></sup>не было</p>'
        '<p>Слово <sup class="note"><a data-note-id="n_1" href="#note-n_1">{1}</a></sup> и <em>курсив</em></p></section>',
        note("n_1", "<p>Один.</p>") + note("n_2", "<p>Два.</p>"),
    )
    a, b, c = book["blocks"][1:]
    assert (a["text"], a["notes"]) == ("Кит. Конец", [{"pos": 3, "id": "n_1", "m": "1"}])
    assert (b["text"], b["notes"]) == ("в организме не было", [{"pos": 11, "id": "n_2", "m": "69"}])
    assert (c["text"], c["notes"], c["em"]) == ("Слово и курсив", [{"pos": 5, "id": "n_1", "m": "1"}], [[8, 14]])


def test_titles_are_stripped_and_joined(tmp_path):  # F26
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p> У Чэн-энь</p><p>Путешествие на Запад<sup class="note">'
        '<a data-note-id="n_1">[1]</a></sup></p></h2><p>Текст.</p></section>'
        '<section id="s2"><h2><p>Глава первая,</p><p>  в которой</p></h2><p>Текст.</p></section>',
        note("n_1", "<p>Один.</p>"),
    )
    assert [c["title"] for c in book["chapters"]] == ["У Чэн-энь. Путешествие на Запад", "Глава первая, в которой"]
    t = book["blocks"][0]
    assert t["text"] == "У Чэн-энь Путешествие на Запад" and t["sentences"] == [[0, len(t["text"])]]


def test_rich_notes(tmp_path):  # F27
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><p>Текст<sup class="note"><a data-note-id="n_1">[1]</a></sup>.</p>'
        '<p>Ещё<sup class="note"><a data-note-id="n_2">[2]</a></sup>.</p></section>',
        note(
            "n_1",
            '<p>Вот эти <em>стихи</em>:</p><div class="poem"><div class="stanza"><p class="verse">Строка раз</p>'
            '<p class="verse">Строка два</p></div><p class="text-author">Поэт</p></div>'
            '<div class="img-wrap"><img data-src="i_001.png"/></div>',
        )
        + note("n_2", "<p>Простая   заметка.</p>"),
    )
    n1 = book["notes"]["n_1"]
    assert n1["text"] == "Вот эти стихи:\n\nСтрока раз\nСтрока два\n\nПоэт"
    assert n1["em"] == [[8, 13]]
    assert n1["kinds"] == [[16, 26, "verse"], [27, 37, "verse"], [39, 43, "text-author"]]
    assert n1["pics"] == [{"pos": len(n1["text"]), "src": "images/i_001.png"}]
    assert book["notes"]["n_2"] == "Простая заметка."
    assert '"n_2": "Простая заметка."' in dump_book(book)


def test_epigraph_keeps_every_child(tmp_path):  # F28
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><div class="epigraph"><p>Эпиграф.</p>'
        '<div class="poem"><div class="stanza"><p class="verse">Стих</p></div></div>'
        '<cite><p>Цитата.</p></cite><div class="empty-line"></div><p class="text-author">Автор</p></div></section>',
    )
    assert kinds(book)[1:] == [
        ("epigraph", "Эпиграф."),
        ("verse", "Стих"),
        ("cite", "Цитата."),
        ("author", "Автор"),
    ]
    assert book["blocks"][4]["st"] == {"g": 1}


def test_br_is_a_line_break(tmp_path):  # F29
    book = fw(tmp_path, '<section id="s1"><h2><p>Глава</p></h2><p>Строка раз<br/>строка два.</p></section>')
    assert book["blocks"][1]["text"] == "Строка раз\nстрока два."


def test_image_at_a_chapter_end_goes_after_the_last_block(tmp_path):
    book = fw(
        tmp_path,
        '<section id="s1"><h2><p>Глава</p></h2><p>Текст.</p><div class="img-wrap"><img data-src="a.png"/></div>'
        '</section><section id="s2"><h2><p>Вторая</p></h2><div class="img-wrap"><img data-src="b.png"/></div>'
        '<p>Текст два.</p><div class="img-wrap"><img data-src="c.png"/></div></section>',
    )
    imgs = [(b["text"], b["images"]) for b in book["blocks"] if b["images"]]
    assert imgs == [
        ("Текст.", [{"src": "images/a.png", "after": True}]),
        ("Текст два.", [{"src": "images/b.png"}, {"src": "images/c.png", "after": True}]),
    ]


def test_generic_page_text_in_lists_cells_and_divs(tmp_path):  # F30
    book = page(
        tmp_path,
        "<div id='main'><h2>Глава I</h2><p>Первый абзац текста. Второе предложение.</p>"
        "<div>Текст прямо в div.</div><ul><li>Пункт один.</li><li>Пункт два.<ul><li>Вложенный.</li></ul></li></ul>"
        "<blockquote>Голая цитата.<p>Цитата в p.</p></blockquote><h4>Подзаголовок</h4>"
        "<table><tr><td><p>Ячейка-раскладка.</p></td></tr></table><!-- комментарий --></div>",
    )
    assert kinds(book) == [
        ("title", "Глава I"),
        ("p", "Первый абзац текста. Второе предложение."),
        ("p", "Текст прямо в div."),
        ("p", "Пункт один."),
        ("p", "Пункт два."),
        ("p", "Вложенный."),
        ("cite", "Голая цитата."),
        ("cite", "Цитата в p."),
        ("subtitle", "Подзаголовок"),
        ("p", "Ячейка-раскладка."),
    ]


def test_generic_page_table(tmp_path):  # F30
    book = page(
        tmp_path,
        "<div><p>Таблица ниже.</p><table><thead><tr><th>Имя</th><th>Год</th></tr></thead>"
        "<tbody><tr><td><em>Лао</em>-цзы</td><td>VI в.</td></tr><tr><td></td><td></td></tr>"
        "<tr><td>Чжуан-цзы</td><td></td></tr></tbody></table></div>",
    )
    t = book["blocks"][1]
    assert t["kind"] == "table" and t["audio"] is False
    assert t["text"] == "Имя\tГод\nЛао-цзы\tVI в.\nЧжуан-цзы\t"
    assert t["rows"] == [[[0, 3, 1], [4, 7, 1]], [[8, 15], [16, 21]], [[22, 31], [32, 32]]]
    assert t["sentences"] == [[0, 7], [8, 21], [22, 31]]  # X17: no tab at a row end
    assert t["em"] == [[8, 11]]


def test_generic_page_footnotes(tmp_path):  # F30
    book = page(
        tmp_path,
        "<div><p>Текст<a href='#fn1' class='footnote-ref' id='fnref1' role='doc-noteref'><sup>1</sup></a>."
        " Ещё <sup class='reference' id='cite_ref-2'><a href='#cite_note-2'>[2]</a></sup> слово."
        " См. главу <sup><a class='reference internal' href='#chapter'>2</a></sup>.</p><h2 id='chapter'>Глава</h2><p>Текст главы.</p>"
        "<section class='footnotes' role='doc-endnotes'><hr/><ol>"
        "<li id='fn1'><p>Первая <em>сноска</em>. <a href='#fnref1' class='footnote-back'>↩︎</a></p></li>"
        "</ol></section><ol class='references'><li id='cite_note-2'><span><a href='#cite_ref-2'>^</a></span>"
        " Вторая сноска.<p>Её второй абзац.</p></li></ol></div>",
    )
    assert kinds(book) == [("p", "Текст. Ещё слово. См. главу 2."), ("title", "Глава"), ("p", "Текст главы.")]
    assert book["blocks"][0]["notes"] == [{"pos": 5, "id": "fn1", "m": "1"}, {"pos": 10, "id": "cite_note-2", "m": "2"}]
    assert book["notes"] == {
        "fn1": {"text": "Первая сноска.", "em": [[7, 13]]},
        "cite_note-2": {"text": "Вторая сноска.\n\nЕё второй абзац."},
    }


def test_generic_page_images_saved_next_to_it(tmp_path):  # F30
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a b.png").write_bytes(b"png")
    book = page(
        tmp_path,
        "<div><p>Первый абзац.</p><figure><img src='pics/a%20b.png' width='10' height='20'/>"
        "<img src='http://x/missing.png'/></figure><p>Второй абзац.</p><p>Конец.</p>"
        "<img src='a%20b.png'/></div>",
    )
    assert [(b["text"], b["images"]) for b in book["blocks"]] == [
        ("Первый абзац.", []),
        ("Второй абзац.", [{"src": "images/a b.png", "w": 10, "h": 20}]),
        ("Конец.", [{"src": "images/a b.png", "after": True}]),
    ]


# the 2026-10 final review (X1-X7, X17, X19, X30, X31)


def test_same_markup_links_are_two_links(tmp_path):  # X1
    book = page(
        tmp_path,
        '<p>Первое слово<sup><a href="#n1">1</a></sup> тут.</p><p>Второе тоже<sup><a href="#n1">1</a></sup> тут.</p>'
        '<aside><p id="n1">Текст сноски.</p></aside>',
    )
    assert [(b["text"], b["notes"]) for b in book["blocks"]] == [
        ("Первое слово тут.", [{"pos": 12, "id": "n1", "m": "1"}]),
        ("Второе тоже тут.", [{"pos": 11, "id": "n1", "m": "1"}]),
    ]
    assert book["notes"] == {"n1": "Текст сноски."}


def test_text_straight_in_the_body_beats_a_small_box(tmp_path):  # X2
    book = page(
        tmp_path,
        "<h1>Глава 1</h1><p>Длинный абзац номер один, в нём много слов.</p><p>Длинный абзац номер два, в нём тоже.</p>"
        '<div class="copy"><p>© 2020</p></div>',
    )
    assert [t for _, t in kinds(book)][:3] == [
        "Глава 1",
        "Длинный абзац номер один, в нём много слов.",
        "Длинный абзац номер два, в нём тоже.",
    ]


def test_chapters_side_by_side_are_all_kept(tmp_path):  # X2
    book = page(
        tmp_path,
        '<div class="chapter"><h2>I</h2><p>Абзац первой главы длинный.</p></div>'
        '<div class="chapter"><h2>II</h2><p>Абзац второй главы длинный длинный.</p></div>',
    )
    assert [c["title"] for c in book["chapters"]] == ["I", "II"]
    assert len(book["blocks"]) == 4


def test_the_article_wins_over_a_sidebar(tmp_path):  # X2
    book = page(
        tmp_path,
        "<div class='side'><p>Реклама.</p></div><div class='content'><p>Длинный текст статьи, очень длинный, "
        "длинный, длинный, длинный.</p><p>Ещё абзац статьи, тоже длинный, длинный, длинный, длинный.</p></div>",
    )
    assert [t for _, t in kinds(book)] == [
        "Длинный текст статьи, очень длинный, длинный, длинный, длинный.",
        "Ещё абзац статьи, тоже длинный, длинный, длинный, длинный.",
    ]


def test_an_article_header_keeps_its_heading(tmp_path):  # X3
    book = page(
        tmp_path,
        "<header><nav><a href='/'>Сайт</a></nav></header><article><header><h1>Глава первая</h1></header>"
        "<p>Текст главы первой довольно длинный.</p></article>",
    )
    assert kinds(book) == [("title", "Глава первая"), ("p", "Текст главы первой довольно длинный.")]


def test_a_back_link_target_stands_for_its_paragraph(tmp_path):  # X4
    book = page(
        tmp_path,
        '<div><p>Слово<a id="ref1" href="#fn1"><sup>1</sup></a> в тексте.</p><p>Ещё абзац текста.</p></div>'
        '<div><p><a id="fn1" href="#ref1">1</a> Текст сноски.</p></div>',
    )
    assert kinds(book) == [("p", "Слово в тексте."), ("p", "Ещё абзац текста.")]
    assert book["blocks"][0]["notes"] == [{"pos": 5, "id": "fn1", "m": "1"}]
    assert book["notes"] == {"fn1": "Текст сноски."}


def test_a_note_holding_a_note_link_is_a_note(tmp_path):  # X5
    book = page(
        tmp_path,
        '<div><p>Слово<sup><a href="#n1">1</a></sup> в тексте.</p><p>Ещё абзац текста.</p></div>'
        '<div><p id="n1">Сноска со ссылкой<sup><a href="#n2">2</a></sup> внутри.</p><p id="n2">Вторая сноска.</p></div>',
    )
    assert kinds(book) == [("p", "Слово в тексте."), ("p", "Ещё абзац текста.")]
    assert book["notes"] == {"n1": "Сноска со ссылкой внутри.", "n2": "Вторая сноска."}


def test_an_anchor_in_the_running_text_is_no_note(tmp_path):  # X5
    book = page(
        tmp_path,
        '<div><p>Слово<sup><a href="#n1">1</a></sup> и якорь <span id="x"></span>тут.</p>'
        '<p>См.<sup><a href="#x">2</a></sup> выше.</p></div><aside><p id="n1">Сноска.</p></aside>',
    )
    assert [t for _, t in kinds(book)] == ["Слово и якорь тут.", "См.2 выше."]
    assert book["notes"] == {"n1": "Сноска."}


def test_sup_note_without_a_link_keeps_its_text(tmp_path):  # X6
    book = page(tmp_path, '<div><p>Слово<sup class="note">*</sup> и ещё.</p></div>')
    assert kinds(book) == [("p", "Слово* и ещё.")]
    assert book["blocks"][0]["sup"] == [[5, 6]]


class Hooks:
    """The epub walker's hooks: a link to #n1 is a note link with the marker `m`."""

    def __init__(self, m: str = "") -> None:
        self.m = m

    def skip(self, el) -> bool:
        return False

    def note_ref(self, a):
        return ("n1", self.m) if a.get("href") == "#n1" else None

    def image(self, el):
        return None


def test_sup_note_is_a_reader_page_reference_only():  # X6
    p = BeautifulSoup('<p>Слово<sup class="note"><a href="#n1">1</a></sup> и ещё.</p>', "html.parser").p
    r = inline_text(p, Hooks("1"))
    assert (r["text"], r["notes"]) == ("Слово и ещё.", [{"pos": 5, "id": "n1", "m": "1"}])


def test_a_quote_after_a_note_link_closes_or_opens(tmp_path):  # X7
    book = page(
        tmp_path,
        '<div><p>Он сказал<sup><a href="#n1">1</a></sup> "Привет" и ушёл.</p>'
        '<p>Слово "в кавычках<sup><a href="#n1">1</a></sup> " и дальше.</p></div><aside><p id="n1">Сноска.</p></aside>',
    )
    assert [(b["text"], b["notes"][0]["pos"]) for b in book["blocks"]] == [
        ('Он сказал "Привет" и ушёл.', 9),
        ('Слово "в кавычках" и дальше.', 17),
    ]


def test_a_quote_after_a_note_link_in_inline_text():  # X7
    def flat(html: str) -> tuple[str, list, list]:
        r = inline_text(BeautifulSoup(html, "html.parser").p, Hooks())
        return r["text"], [n["pos"] for n in r["notes"]], r["em"]

    assert flat('<p>сказал <a href="#n1">1</a> "Привет"</p>') == ('сказал "Привет"', [6], [])
    assert flat('<p>"слово <a href="#n1">1</a> " и</p>') == ('"слово" и', [6], [])
    assert flat('<p><i>"слово <a href="#n1">1</a> "</i></p>') == ('"слово"', [6], [[0, 7]])
    assert flat("<p>слово <a href=\"#n1\">1</a> 'да'.</p>") == ("слово 'да'.", [5], [])


def test_a_poem_without_stanzas_in_a_note_is_one_stanza(tmp_path):  # X19
    book = page(
        tmp_path,
        '<div><p>Слово<sup><a href="#n1">1</a></sup> тут.</p><p>абзац</p></div>'
        '<div><div id="n1"><div class="poem"><p>Строка один</p><p>Строка два</p></div></div></div>',
    )
    assert book["notes"]["n1"]["text"] == "Строка один\nСтрока два"


def test_many_notes_are_found_quickly(tmp_path):  # X30
    n = 2000
    body = "".join(f'<p>Абзац {i} текст<sup><a href="#n{i}">{i}</a></sup> дальше.</p>' for i in range(n))
    notes = "".join(f'<p id="n{i}">Сноска {i}.</p>' for i in range(n))
    t = time.monotonic()
    book = page(tmp_path, f"<div>{body}</div><div>{notes}</div>")
    assert len(book["notes"]) == n
    assert time.monotonic() - t < 2.0


def test_sizes_that_are_not_numbers_are_left_out(tmp_path):  # X31
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "pic.png").write_bytes(b"png")
    book = page(tmp_path, '<div><p>Текст.</p><img src="pic.png" width="100%" height="50"/><p>Ещё.</p></div>')
    assert book["blocks"][1]["images"] == [{"src": "images/pic.png"}]
    book = fw(
        tmp_path,
        '<section id="s1"><h2>Гл</h2><div class="img-wrap"><img src="pic.png" width="100%" height="50"/></div>'
        "<p>Текст.</p></section>",
    )
    assert book["blocks"][1]["images"] == [{"src": "images/pic.png"}]
