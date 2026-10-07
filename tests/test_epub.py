"""EPUB extraction (pipeline/extract_epub.py) on small made-up books built in memory: notes, pictures, text
outside paragraphs, quotes, the package file, verse and epigraphs, chapters, marks, line breaks and tables."""

from __future__ import annotations

import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from extract_epub import TOO_BIG, extract  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 8
HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"'
    ' xmlns:epub="http://www.idpf.org/2007/ops"><head><title>x</title></head><body>'
)


def make(tmp_path: Path, docs: dict[str, str], opf: str | None = None, files: dict | None = None) -> dict:
    """An epub of `docs` (name -> body markup) in spine order, extracted."""
    if opf is None:
        items = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(docs))
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Проба</dc:title></metadata>'
            f"<manifest>{items}</manifest><spine>{spine}</spine></package>"
        )
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   "</rootfiles></container>")  # fmt: skip
        z.writestr("OEBPS/content.opf", opf)
        for name, body in docs.items():
            z.writestr("OEBPS/" + name, HEAD + body + "</body></html>")
        for name, data in (files or {}).items():
            z.writestr("OEBPS/" + name, data)
    return extract(p)


def texts(book: dict) -> list[str]:
    return [b["text"] for b in book["blocks"]]


# ---------------------------------------------------------------- F40 notes


def test_noteref_to_an_aside_footnote_is_a_note_and_the_aside_leaves_the_text(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": '<p>Кит <a epub:type="noteref" href="#fn1">{1}</a>. Плыл дальше.</p>'
            '<aside epub:type="footnote" id="fn1"><p>Большая <em>рыба</em>.</p></aside>'
            '<aside epub:type="footnote" id="fn9"><p>Ни на что не указывает.</p></aside><p>Конец.</p>'
        },
    )
    assert texts(book) == ["Кит. Плыл дальше.", "Ни на что не указывает.", "Конец."]  # X24: unlinked, so text
    assert book["blocks"][0]["notes"] == [{"pos": 3, "id": "fn1", "m": "1"}]
    assert book["notes"] == {"fn1": {"text": "Большая рыба.", "em": [[8, 12]], "strong": [], "sup": [], "sub": [],
                                     "pics": [], "kinds": []}}  # fmt: skip


def test_endnotes_in_another_document_with_back_links(tmp_path):
    book = make(
        tmp_path,
        {
            "text.xhtml": '<p>Слово<a role="doc-noteref" href="notes.xhtml#n1" id="r1">1</a> и ещё'
            '<sup><a href="notes.xhtml#n2" id="r2">[2]</a></sup>.</p>',
            "notes.xhtml": '<h2>Примечания</h2><section epub:type="endnotes"><ol>'
            '<li id="n1"><p>Первое.</p><p>Второй абзац.</p> <a epub:type="backlink" href="text.xhtml#r1">↩</a></li>'
            '<li id="n2"><p><a href="text.xhtml#r2">2</a>. Второе.</p></li></ol></section>',
        },
    )
    assert texts(book) == ["Слово и ещё.", "Примечания"]
    assert book["blocks"][0]["notes"] == [{"pos": 5, "id": "n1", "m": "1"}, {"pos": 11, "id": "n2", "m": "2"}]
    n1 = book["notes"]["n1"]
    assert n1["text"] == "Первое.\n\nВторой абзац."
    assert book["notes"]["n2"]["text"] == "Второе."


def test_sup_links_to_short_targets_are_notes_cross_references_are_not(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>В организме<sup><a href='b.xhtml#x1'>[69]</a></sup>не было <a href='#y'><sup>*</sup></a>"
            " сил. См. <a href='b.xhtml#ch'>главу</a>.</p><p id='y'>Сноска звёздочкой.</p>",
            "b.xhtml": "<h2 id='ch'>Глава</h2><p id='x1'>Шестьдесят девятая.</p>"
            '<div class="footnote" id="f"><p>Не указана.</p></div>',
        },
    )
    assert texts(book)[0] == "В организме не было сил. См. главу."
    assert [n["id"] for n in book["blocks"][0]["notes"]] == ["x1", "y"]
    assert [v["text"] for v in book["notes"].values()] == ["Шестьдесят девятая.", "Сноска звёздочкой."]
    assert "Сноска звёздочкой." not in texts(book) and "Шестьдесят девятая." not in texts(book)
    assert "Не указана." in texts(book)  # a footnote nobody links to stays where it is


def test_footnote_class_target_and_backlink_by_marker(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": '<p>Текст<a id="r1" href="#f1">[1]</a>.</p>'
            '<div class="footnote"><p><a id="f1"></a><a href="#r1">[1]</a> Пояснение, см. <a href="#r1">выше</a>.</p>'
            "</div>"
        },
    )
    assert texts(book) == ["Текст."]
    assert book["notes"]["f1"]["text"] == "Пояснение, см. выше."


# ---------------------------------------------------------------- F41 pictures


def test_pictures_inline_alone_svg_and_at_a_chapter_end(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<h1>Один</h1><p>Знак <img src='i/a.png'/> в строке.</p><p><img src='i/b%20c.png'/></p>"
            "<p>После картинки.</p><div><svg><image xlink:href='i/a.png'/></svg></div>",
            "b.xhtml": "<h1>Два</h1><p>Текст.</p><p><img src='i/a.png'/></p>",
        },
        files={"i/a.png": PNG, "i/b c.png": PNG},
    )
    b = book["blocks"]
    assert b[1]["text"] == "Знак в строке." and b[1]["pics"] == [{"pos": 4, "src": "images/a.png"}]
    # the picture alone goes before the next block; the svg picture closes chapter one, after its last block
    assert b[2]["text"] == "После картинки."
    assert b[2]["images"] == [{"src": "images/b_c.png"}, {"src": "images/a.png", "after": True}]
    assert b[3]["text"] == "Два" and b[3]["images"] == []
    assert b[-1]["images"] == [{"src": "images/a.png", "after": True}]


# ---------------------------------------------------------------- F42 text outside paragraphs, lists


def test_bare_text_in_containers_is_read(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": '<div class="paragraph">Абзац в диве.</div><div>Голый текст <b>жирный</b><p>Абзац.</p>хвост.</div>'
            "<dl><dt>Термин</dt><dd>Определение.</dd></dl><pre>код\n  строка</pre>"
            "<figure><img src='x.png'/><figcaption>Подпись.</figcaption></figure>"
            "<ol start='3'><li>Третий.</li><li value='7'><p>Седьмой.</p><ul><li>Пункт.</li></ul></li></ol>"
            "<ul><li>• Уже с точкой.</li></ul><span>Текст в span.</span>"
        },
        files={"x.png": PNG},
    )
    assert texts(book) == [
        "Абзац в диве.", "Голый текст жирный", "Абзац.", "хвост.", "Термин", "Определение.", "код\nстрока",
        "Подпись.", "3. Третий.", "7. Седьмой.", "• Пункт.", "• Уже с точкой.", "Текст в span.",
    ]  # fmt: skip
    assert book["blocks"][1]["strong"] == [[12, 18]]
    assert book["blocks"][7]["images"] == [{"src": "images/x.png"}]


# ---------------------------------------------------------------- F43 block quotes


def test_blockquote_keeps_all_its_children(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>Начало.</p><blockquote>Голый текст.<h4>Заголовок</h4><p>Абзац.</p><div>Див.</div>"
            "<ul><li>Пункт.</li></ul></blockquote>"
        },
    )
    assert [(b["kind"], b["text"]) for b in book["blocks"][1:]] == [
        ("cite", "Голый текст."), ("title", "Заголовок"), ("cite", "Абзац."), ("cite", "Див."), ("cite", "• Пункт."),
    ]  # fmt: skip


# ---------------------------------------------------------------- F44 the package file


def test_opf_quotes_prefixes_encoded_hrefs_duplicates_and_missing_items(tmp_path):
    opf = (
        "<opf:package xmlns:opf='http://www.idpf.org/2007/opf' version='2.0'><opf:metadata>"
        "<dc:title>Т &amp; Т</dc:title><dc:creator opf:role='aut'>Автор</dc:creator></opf:metadata><opf:manifest>"
        "<opf:item id='a' href='Text/Chapter%201.xhtml' media-type='application/xhtml+xml'/>"
        '<opf:item href="Text/b.xhtml" id="b" media-type="application/xhtml+xml" />'
        "</opf:manifest><opf:spine><opf:itemref idref='a'/><opf:itemref idref='zzz'/><opf:itemref idref='b'/>"
        "<opf:itemref idref='a'/></opf:spine></opf:package>"
    )
    book = make(tmp_path, {"Text/Chapter 1.xhtml": "<h1>Первая</h1><p>Раз.</p>", "Text/b.xhtml": "<p>Два.</p>"}, opf)
    assert book["title"] == "Т & Т" and book["author"] == "Автор"
    assert texts(book) == ["Первая", "Раз.", "Два."]


# ---------------------------------------------------------------- F45 verse and epigraphs


def test_verse_and_epigraph_from_classes_types_and_shape(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<h2>Глава</h2><blockquote><p>Эпиграф без класса.</p></blockquote><p>Текст.</p>"
            '<div epub:type="z3998:poem"><p><span>Строка раз,</span><br/>\n<span>строка два.</span></p>'
            "<p>Вторая строфа,<br/>её строка.</p></div>"
            '<div class="poem"><div class="stanza"><p>Один</p><p>два</p></div></div>'
            "<p>Короткая,<br/><br/>в две строки.</p><blockquote><p>Цитата.</p></blockquote>"
        },
    )
    got = [(b["kind"], b["stanza"], b["text"]) for b in book["blocks"]]
    assert got == [
        ("title", None, "Глава"),
        ("epigraph", None, "Эпиграф без класса."),
        ("p", None, "Текст."),
        ("verse", 1, "Строка раз,\nстрока два."),
        ("verse", 2, "Вторая строфа,\nеё строка."),
        ("verse", 3, "Один"),
        ("verse", 3, "два"),
        ("p", None, "Короткая,\n\nв две строки."),  # X26: two lines are an address, not a poem
        ("cite", None, "Цитата."),
    ]


# ---------------------------------------------------------------- F46 chapters, marks, breaks, tables


def test_chapters_from_nav_with_levels(tmp_path):
    nav = (
        '<nav epub:type="toc"><ol><li><a href="a.xhtml">Часть 1</a><ol><li><a href="a.xhtml#c1">Глава 1</a></li>'
        '<li><span>Без ссылки</span><ol><li><a href="b.xhtml#c2">Глава 2</a></li></ol></li></ol></li></ol></nav>'
    )
    opf = (
        '<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/><item id="a" href="a.xhtml" '
        'media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'
    )
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>Пролог.</p><h3 id='c1'>Глава первая</h3><p>Текст.</p>",
            "b.xhtml": "<p>Ещё.</p><p><a id='c2'/>Вторая.</p>",
            "nav.xhtml": nav,
        },
        opf,
    )
    assert [(c["title"], c["level"], c["first_block"]) for c in book["chapters"]] == [
        ("Часть 1", 1, 0), ("Глава 1", 2, 1), ("Глава 2", 3, 4),
    ]  # fmt: skip
    assert book["blocks"][1]["kind"] == "title"


def test_chapters_from_ncx(tmp_path):
    ncx = (
        "<ncx><navMap><navPoint id='p1'><navLabel><text>Первая</text></navLabel><content src='a.xhtml'/>"
        "<navPoint id='p2'><navLabel><text> Вложенная </text></navLabel><content src='a.xhtml#x'/></navPoint>"
        "</navPoint></navMap></ncx>"
    )
    opf = (
        '<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id="ncx" href="toc.ncx" '
        'media-type="application/x-dtbncx+xml"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine toc="ncx"><itemref idref="a"/></spine></package>'
    )
    book = make(tmp_path, {"a.xhtml": "<p>Раз.</p><div id='x'><p>Два.</p></div>"}, opf, {"toc.ncx": ncx})
    assert [(c["title"], c["level"], c["first_block"]) for c in book["chapters"]] == [("Первая", 1, 0),
                                                                                     ("Вложенная", 2, 1)]  # fmt: skip


def test_headings_h1_to_h4_without_a_toc_titles_without_note_text(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<h1>Один<a href='#n' epub:type='noteref'>1</a></h1><h2>Два,<br/>подзаголовок</h2>"
            "<h3>Три<br/>строка</h3><h4>Четыре</h4><h5>Пять</h5><p>Т.</p><aside epub:type='footnote' id='n'>Н.</aside>"
        },
    )
    assert [(c["title"], c["level"]) for c in book["chapters"]] == [
        ("Один", 1), ("Два, подзаголовок", 2), ("Три. строка", 3), ("Четыре", 4),
    ]  # fmt: skip
    assert [b["kind"] for b in book["blocks"]] == ["title", "title", "title", "title", "subtitle", "p"]


def test_marks_and_line_breaks(tmp_path):
    book = make(tmp_path, {"a.xhtml": "<p><b>Жир</b> <i>курсив</i> H<sub>2</sub>O x<sup>3</sup> <strong>a <em>b</em></strong>"
                           "<br/>новая строка</p>"})  # fmt: skip
    b = book["blocks"][0]
    assert b["text"] == "Жир курсив H2O x3 a b\nновая строка"
    assert b["strong"] == [[0, 3], [18, 21]] and b["em"] == [[4, 10], [20, 21]]
    assert b["sub"] == [[12, 13]] and b["sup"] == [[16, 17]]


def test_tables(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<table><caption>Таблица.</caption><thead><tr><th>Имя</th><th>Число</th></tr></thead>"
            "<tbody><tr><td>Лёд<br/>вода</td><td><em>4</em></td></tr><tr><td></td><td></td></tr>"
            "<tr><td></td><td>5 <span>внутри</span></td></tr></tbody></table>"
        },
    )
    cap, t = book["blocks"]
    assert cap["text"] == "Таблица."
    assert t["kind"] == "table" and t["audio"] is False
    assert t["text"] == "Имя\tЧисло\nЛёд вода\t4\n\t5 внутри"
    assert t["rows"] == [[[0, 3, 1], [4, 9, 1]], [[10, 18], [19, 20]], [[21, 21], [22, 30]]]
    assert t["sentences"] == [[0, 9], [10, 20], [22, 30]]
    assert t["em"] == [[19, 20]]


def test_layout_tables_are_read_as_blocks(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<table><tr><td>Данные</td><td>1</td></tr><tr><td>Ещё</td><td>2</td></tr></table>"
            "<table role='presentation'><tr><td>Роль</td><td>презентации</td></tr><tr><td>а</td><td>б</td></tr></table>"
            "<table><tr><td>Одна колонка.</td></tr><tr><td>Вторая строка.</td></tr></table>"
            "<table><tr><td>Одна строка</td><td>две ячейки</td></tr></table>"
            "<table><tr><td><p>Абзац в ячейке.</p></td><td>x</td></tr><tr><td>y</td><td>"
            "<table><tr><td>Вложенная</td><td>таблица</td></tr><tr><td>в</td><td>г</td></tr></table></td></tr></table>"
            "<table><tr><td>"
            + "Длинный текст ячейки. " * 6
            + "</td><td>z</td></tr><tr><td>u</td><td>v</td></tr></table>"
        },
    )
    assert [(b["kind"], b["text"]) for b in book["blocks"]] == [
        ("table", "Данные\t1\nЕщё\t2"),
        ("p", "Роль"), ("p", "презентации"), ("p", "а"), ("p", "б"),
        ("p", "Одна колонка."), ("p", "Вторая строка."),
        ("p", "Одна строка"), ("p", "две ячейки"),
        ("p", "Абзац в ячейке."), ("p", "x"), ("p", "y"), ("p", "Вложенная"), ("p", "таблица"), ("p", "в"), ("p", "г"),
        ("p", ("Длинный текст ячейки. " * 6).strip()), ("p", "z"), ("p", "u"), ("p", "v"),
    ]  # fmt: skip


def test_site_chrome_and_the_contents_document_are_not_read(tmp_path):
    nav = '<nav epub:type="toc"><ol><li><a href="a.xhtml">Глава</a></li></ol></nav><p>Текст оглавления.</p>'
    opf = (
        '<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/><item id="a" href="a.xhtml" '
        'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="nav"/><itemref idref="a"/></spine>'
        "</package>"
    )
    book = make(
        tmp_path,
        {
            "nav.xhtml": nav,
            "a.xhtml": "<div class='ws-header'>Шапка</div><p>Текст<span class='mw-editsection'>[править]</span>.</p>"
            "<div class='licenseContainer licenseBanner'><p>Лицензия.</p></div><div id='headertemplate'>Ш</div>"
            "<table class='navbox'><tr><td>a</td><td>b</td></tr></table><section epub:type='toc'><p>Оглавление</p>"
            "</section><div class='wst-auxtoc'><ul><li>Глава 2</li></ul></div><p class='noprint'>Не печатать</p>",
        },
        opf,
    )
    assert texts(book) == ["Текст."]
    assert [c["title"] for c in book["chapters"]] == ["Глава"]


def test_the_wikisource_credits_page_is_not_read(tmp_path):
    nav = '<nav epub:type="toc"><ol><li><a href="a.xhtml">Глава</a></li><li><a href="b.xhtml">Об издании</a></li></ol></nav>'
    opf = (
        '<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" '
        'media-type="application/xhtml+xml" properties="nav"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/><item id="c" href="c.xhtml" '
        'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/>'
        '<itemref idref="c"/></spine></package>'
    )
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   "</rootfiles></container>")  # fmt: skip
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", HEAD + nav + "</body></html>")
        z.writestr("OEBPS/a.xhtml", HEAD + "<p>Текст.</p></body></html>")
        z.writestr("OEBPS/b.xhtml", "<html><head><title>MediaWiki:Wsexport_about</title></head><body><h1>About this "
                   "digital edition</h1><p>Wikisource.</p></body></html>")  # fmt: skip
        z.writestr("OEBPS/c.xhtml", HEAD + "<div id='ws-contributor'><ul><li>Someone</li></ul></div></body></html>")
    book = extract(p)
    assert texts(book) == ["Текст."]
    assert [c["title"] for c in book["chapters"]] == ["Глава"]


# ---------------------------------------------------------------- review fixes (X12, X20-X29, W1-W4, W14-W16)


def package(tmp_path: Path, docs: dict[str, bytes], container: str = "META-INF/container.xml",
            more: list[tuple[str, bytes]] | None = None) -> Path:  # fmt: skip
    """An epub of raw documents (name -> bytes, spine order) and other entries, written as given."""
    items = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    opf = f"<package><metadata><dc:title>Т</dc:title></metadata><manifest>{items}</manifest><spine>{spine}</spine></package>"
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(container, '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        for name, data in docs.items():
            z.writestr("OEBPS/" + name, data)
        for name, data in more or []:
            z.writestr(name, data)
    return p


def test_an_empty_book_has_chapter_s0_like_txt_and_html(tmp_path):
    book = make(tmp_path, {"a.xhtml": ""})
    assert book["blocks"] == []
    assert book["chapters"] == [{"id": "s0", "title": "", "level": 1, "first_block": 0}]


def test_a_note_keeps_its_leading_dots_and_loses_only_what_its_back_link_leaves(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>Текст<a href='#n1'><sup>1</sup></a> и<a href='#n2' id='r2'><sup>2</sup></a>.</p>"
            "<aside epub:type='footnote' id='n1'><p>...и далее по тексту.</p></aside>"
            "<div class='footnote' id='n2'><p><a href='#r2'>2</a>. Вторая.</p></div>"
        },
    )
    assert book["notes"]["n1"]["text"] == "...и далее по тексту."
    assert book["notes"]["n2"]["text"] == "Вторая."


def test_pictures_get_a_name_each_and_are_stored_once(tmp_path):
    nfd = unicodedata.normalize("NFD", "рйс½.png")
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>Раз <img src='i1/pic.png'/> два <img src='i2/pic.png'/> три <img src='i1/pic.png'/>.</p>"
            f"<p>Имена <img src='i3/{nfd}'/> <img src='i3/a b+c.png'/> <img src='i3/Ке\u0301ды.png'/> "
            "<img src='i3/PIC.png'/>.</p>"
        },
        files={
            "i1/pic.png": b"ONE",
            "i2/pic.png": b"TWO",
            f"i3/{nfd}": PNG,
            "i3/a b+c.png": PNG,
            "i3/Ке\u0301ды.png": PNG,
            "i3/PIC.png": b"THREE",
        },  # fmt: skip
    )
    assert [p["src"] for p in book["blocks"][0]["pics"]] == ["images/pic.png", "images/pic-2.png", "images/pic.png"]
    assert [p["src"] for p in book["blocks"][1]["pics"]] == [
        "images/рйс½.png", "images/a_b_c.png", "images/Ке_ды.png", "images/PIC-3.png",
    ]  # fmt: skip
    images = tmp_path / "images"
    assert (images / "pic.png").read_bytes() == b"ONE" and (images / "pic-2.png").read_bytes() == b"TWO"
    assert (images / "PIC-3.png").read_bytes() == b"THREE"
    assert len(list(images.iterdir())) == 6


def test_a_heading_of_only_a_picture_opens_no_chapter(tmp_path):
    book = make(tmp_path, {"a.xhtml": "<h1>Один</h1><p>Раз.</p><h2><img src='x.png'/></h2><h2> </h2><p>Два.</p>"},
                files={"x.png": PNG})  # fmt: skip
    assert [(c["title"], c["first_block"]) for c in book["chapters"]] == [("Один", 0)]
    assert book["blocks"][2]["text"] == "Два." and book["blocks"][2]["images"] == [{"src": "images/x.png"}]


def test_a_paragraph_of_only_a_note_link_leaves_its_anchor(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p><sup><a href='#n2'>2</a></sup></p><p>Начало.</p><p><a href='#n1'><sup>1</sup></a></p>"
            "<p>Конец.</p><aside epub:type='footnote' id='n1'><p>Первая.</p></aside>"
            "<aside epub:type='footnote' id='n2'><p>Вторая.</p></aside>"
        },
    )
    assert texts(book) == ["Начало.", "Конец."]
    assert book["blocks"][0]["notes"] == [{"pos": 0, "id": "n2", "m": "2"}, {"pos": 7, "id": "n1", "m": "1"}]
    assert sorted(book["notes"]) == ["n1", "n2"]


def test_an_aside_footnote_nobody_links_to_is_text(tmp_path):
    book = make(tmp_path, {"a.xhtml": "<p>Текст.</p><aside role='doc-footnote' id='x'><p>Сама по себе.</p></aside>"})
    assert texts(book) == ["Текст.", "Сама по себе."] and book["notes"] == {}


def test_pre_with_blocks_keeps_its_line_breaks(tmp_path):
    book = make(tmp_path, {"a.xhtml": "<pre>строка один\n  строка два<div>див\nвнутри</div>хвост\nконец</pre>"})
    assert texts(book) == ["строка один\nстрока два", "див\nвнутри", "хвост\nконец"]


def test_two_or_three_lines_are_an_address_four_short_ones_a_poem(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<p>Москва, ул. Тверская, 1,<br/>Ивану Петрову</p><p>Ваш,<br/>И. П.,<br/>1900</p>"
            "<p>Раз,<br/>два,<br/>три,<br/>четыре.</p><div class='poem'><p>Стих,<br/>второй.</p></div>"
        },
    )
    assert [(b["kind"], b["text"]) for b in book["blocks"]] == [
        ("p", "Москва, ул. Тверская, 1,\nИвану Петрову"), ("p", "Ваш,\nИ. П.,\n1900"),
        ("verse", "Раз,\nдва,\nтри,\nчетыре."), ("verse", "Стих,\nвторой."),
    ]  # fmt: skip


def test_list_items_are_numbered_whatever_their_text_starts_with(tmp_path):
    book = make(
        tmp_path,
        {
            "a.xhtml": "<ol><li>A. Первый.</li><li>I. Второй.</li><li>— Третий.</li><li>4. Четвёртый.</li></ol>"
            "<ul><li>— реплика</li><li>• уже с точкой</li></ul>"
        },
    )
    assert texts(book) == [
        "1. A. Первый.", "2. I. Второй.", "3. — Третий.", "4. Четвёртый.", "• — реплика", "• уже с точкой",
    ]  # fmt: skip


def test_list_numbers_of_any_size(tmp_path):
    big = "9" * 101
    book = make(
        tmp_path,
        {
            "a.xhtml": "<ol start='9223372036854775807'><li>а</li><li>б</li></ol><ol start=' -2 '><li>в</li><li>г</li>"
            f"<li>д</li></ol><ol><li value='007'>е</li><li>ж</li></ol><ol start='{big}'><li>з</li></ol>"
            "<ol start='99999999999999999999'><li>и</li><li>к</li></ol>"
        },
    )
    assert texts(book) == [
        "9223372036854775807. а", "9223372036854775808. б", "-2. в", "-1. г", "0. д", "7. е", "8. ж", "1. з",
        "99999999999999999999. и", "100000000000000000000. к",
    ]  # fmt: skip


def test_container_any_case_and_documents_in_their_declared_charset(tmp_path):
    text = "<html><body><p>Привет, мир.</p></body></html>"
    docs = {
        "a.xhtml": ('<?xml version="1.0" encoding="windows-1251"?>' + text).encode("cp1251"),
        "b.xhtml": (
            '<html><head><meta http-equiv="Content-Type" content="text/html; charset=KOI8-R"/></head>'
            "<body><p>Ёлка.</p></body></html>"
        ).encode("koi8_r"),  # fmt: skip
        "c.xhtml": b"\xff\xfe" + "<html><body><p>Шестнадцать.</p></body></html>".encode("utf-16-le"),
        "d.xhtml": b"\xef\xbb\xbf" + "<p>С меткой.</p>".encode(),
        "e.xhtml": '<?xml version="1.0" encoding="gb2312"?><p>Неизвестная — значит UTF-8.</p>'.encode(),
    }
    book = extract(package(tmp_path, docs, container="meta-inf/Container.XML"))
    assert texts(book) == ["Привет, мир.", "Ёлка.", "Шестнадцать.", "С меткой.", "Неизвестная — значит UTF-8."]


def nav_book(tmp_path: Path, nav: str, docs: dict[str, str]) -> dict:
    items = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, n in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    opf = (
        '<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id="nav" href="nav.xhtml" '
        f'media-type="application/xhtml+xml" properties="nav"/>{items}</manifest><spine>{spine}</spine></package>'
    )
    return make(tmp_path, {**docs, "nav.xhtml": f'<nav epub:type="toc"><ol>{nav}</ol></nav>'}, opf)


def test_the_toc_is_kept_despite_headings_inside_chapters_and_notes(tmp_path):
    sub = "<h3>Часть</h3><p>Т.</p>" * 4
    book = nav_book(
        tmp_path,
        "".join(f"<li><a href='{d}.xhtml'>Глава {d}</a></li>" for d in "abc"),
        {
            **{f"{d}.xhtml": f"<h2>Глава {d}</h2>{sub}" for d in "abc"},
            "n.xhtml": "<section epub:type='endnotes'>" + "<h3>Примечание</h3><p>П.</p>" * 9 + "</section>",
        },
    )
    assert [c["title"] for c in book["chapters"]] == ["Глава a", "Глава b", "Глава c"]


def test_a_toc_of_files_or_of_few_documents_gives_way_to_headings(tmp_path):
    chapters = "".join(f"<h3>{i}</h3><p>Текст {i}.</p>" for i in range(1, 8))
    book = nav_book(tmp_path, "<li><a href='t.xhtml'>Титул</a></li><li><a href='b.xhtml'>Книга</a></li>",
                    {"t.xhtml": "<p>Титул.</p>", "b.xhtml": "<h2>Книга</h2><p>Автор.</p>" + chapters})  # fmt: skip
    assert [c["title"] for c in book["chapters"]] == ["", "Книга", *map(str, range(1, 8))]
    docs = {f"{d}.xhtml": f"<h1>Глава {d}</h1><p>Т.</p>" for d in "abcde"}
    book = nav_book(tmp_path, "<li><a href='a.xhtml'>А</a></li><li><a href='b.xhtml'>Б</a></li>", docs)
    assert [c["title"] for c in book["chapters"]] == [f"Глава {d}" for d in "abcde"]
    book = nav_book(tmp_path, "<li><a href='a.xhtml'>Одна</a></li>", {"a.xhtml": "<h1>Глава</h1><p>Т.</p><h1>Ещё</h1>"})
    assert [c["title"] for c in book["chapters"]] == ["Глава", "Ещё"]


def test_more_than_500_mb_unpacked_is_refused_before_reading(tmp_path):
    p = package(tmp_path, {"a.xhtml": HEAD.encode() + b"<p>x</p></body></html>"}, more=[("OEBPS/big.bin", b"0")])
    data = bytearray(p.read_bytes())
    cd = data.rindex(b"PK\x01\x02")  # the last entry's central record: its size field says 600 MB
    data[cd + 24 : cd + 28] = (600_000_000).to_bytes(4, "little")
    p.write_bytes(bytes(data))
    with pytest.raises(SystemExit, match=TOO_BIG):
        extract(p)


def test_names_compared_in_nfc_and_the_last_of_two_entries(tmp_path):
    nfd = unicodedata.normalize("NFD", "глава й.xhtml")
    p = tmp_path / "book.epub"
    opf = (
        "<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id='a' href='"
        + unicodedata.normalize("NFC", "глава й.xhtml")
        + "' media-type='application/xhtml+xml'/><item id='b' href='b.xhtml' media-type='application/xhtml+xml'/>"
        "</manifest><spine><itemref idref='a'/><itemref idref='b'/></spine></package>"
    )
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/>'
                   "</rootfiles></container>")  # fmt: skip
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/" + nfd, "<p>Найдена.</p>")
        z.writestr("OEBPS/b.xhtml", "<p>Первая запись.</p>")
        with pytest.warns(UserWarning, match="Duplicate name"):
            z.writestr("OEBPS/b.xhtml", "<p>Последняя запись.</p>")
    assert texts(extract(p)) == ["Найдена.", "Последняя запись."]


def test_deep_nesting_is_flattened_not_a_crash(tmp_path):
    divs = "<div><span>" * 400 + "слово " * 50 + "</span></div>" * 400
    paras = "<p>слово" * 3000
    book = make(tmp_path, {"a.xhtml": f"<p>Начало.</p>{divs}<p>Середина.</p>", "b.xhtml": paras})
    assert texts(book)[0] == "Начало." and texts(book)[2] == "Середина."
    assert texts(book)[1].split() == ["слово"] * 50
    assert sum(len(t.split()) for t in texts(book)[3:]) == 3000
