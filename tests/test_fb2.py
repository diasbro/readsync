import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from extract_fb2 import extract  # noqa: E402


def bins(*names: str) -> str:
    """Binaries for these pictures: a picture without one is not referenced."""
    import base64

    data = base64.b64encode(b"picture").decode()
    return "".join(f'<binary id="{n}" content-type="image/png">{data}</binary>' for n in names)


FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>Проба</book-title></title-info></description>
<body><title><p>Проба</p></title>
<section><title><p>Глава первая</p></title>
<p>Первое предложение. Второе <emphasis>с курсивом</emphasis> предложение<a l:href="#n1" type="note">[1]</a>.</p>
<poem><stanza><v>Строка раз</v><v>   Строка два</v></stanza></poem>
<empty-line/>
</section>
<section><title><p>Глава вторая</p></title><p>Текст.</p></section>
</body>
<body name="notes"><section id="n1"><title><p>1</p></title><p>Это сноска.</p></section></body>
</FictionBook>"""


def test_fb2_extract(tmp_path):
    f = tmp_path / "book.fb2"
    f.write_text(FB2, encoding="utf-8")
    book = extract(f)
    assert book["title"] == "Проба" and book["author"] == "Иван Иванов"
    assert [c["title"] for c in book["chapters"]] == ["Проба", "Глава первая", "Глава вторая"]
    kinds = [b["kind"] for b in book["blocks"]]
    assert kinds == ["title", "title", "p", "verse", "verse", "title", "p"]
    p = book["blocks"][2]
    assert len(p["sentences"]) == 2
    assert p["text"][p["em"][0][0] : p["em"][0][1]] == "с курсивом"
    assert p["notes"] == [{"pos": len(p["text"]) - 1, "id": "n1", "m": "1"}]
    assert book["notes"]["n1"] == "Это сноска."
    assert book["blocks"][4]["text"].startswith("   Строка два")
    assert book["blocks"][3]["stanza"] == book["blocks"][4]["stanza"] == 1


def test_fb2_images(tmp_path):
    import base64

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 8).decode()
    fb2 = FB2.replace("<p>Текст.</p>", '<image l:href="#pic.png"/><p>Текст.</p>').replace(
        "</FictionBook>", f'<binary id="pic.png" content-type="image/png">{png}</binary></FictionBook>'
    )
    (tmp_path / "book.fb2").write_text(fb2, encoding="utf-8")
    book = extract(tmp_path / "book.fb2")
    assert [b["images"] for b in book["blocks"] if b["images"]] == [[{"src": "images/pic.png"}]]
    assert (tmp_path / "images" / "pic.png").exists()


def test_fb2_zip(tmp_path):
    import zipfile

    z = tmp_path / "book.fb2.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("book.fb2", FB2)
    assert extract(z)["title"] == "Проба"


def test_fb2_binary_ids_stay_inside_images(tmp_path):
    """A binary id comes from the file: `../` or an absolute path in it names a picture in images/, nothing else."""
    import base64

    part = tmp_path / "work" / "b" / "parts" / "01"
    part.mkdir(parents=True)
    data = base64.b64encode(b"picture").decode()
    outside = tmp_path / "absolute.jpg"
    fb2 = FB2.replace("<p>Текст.</p>", '<image l:href="#../../../up.jpg"/><p>Текст.</p>').replace(
        "</FictionBook>",
        f'<binary id="../../../up.jpg" content-type="image/jpeg">{data}</binary>'
        f'<binary id="{outside}" content-type="image/jpeg">{data}</binary>'
        f'<binary id=".." content-type="image/jpeg">{data}</binary></FictionBook>',
    )
    (part / "book.fb2").write_text(fb2, encoding="utf-8")

    book = extract(part / "book.fb2")

    assert sorted(p.name for p in (part / "images").iterdir()) == ["absolute.jpg", "up.jpg"]
    assert not outside.exists() and not (tmp_path / "work" / "b" / "up.jpg").exists()
    assert [b["images"] for b in book["blocks"] if b["images"]] == [[{"src": "images/up.jpg"}]]


def test_fb2_comment_links_are_notes_and_stay_out_of_titles(tmp_path):
    fb2 = (
        FB2.replace(
            "<p>Глава вторая</p>", '<p>Глава <emphasis>вторая</emphasis><a l:href="#c7" type="comment">{7}</a></p>'
        )
        .replace("<p>Текст.</p>", '<p>Текст<a l:href="#c7" type="comment">{7}</a>.</p>')
        .replace(
            "<body><title><p>Проба</p></title>", '<body><title><p>Проба<a l:href="#n1" type="note">[1]</a></p></title>'
        )
    )
    fb2 = fb2.replace(
        "</FictionBook>", '<body name="comments"><section id="c7"><p>Комментарий.</p></section></body></FictionBook>'
    )
    (tmp_path / "book.fb2").write_text(fb2, encoding="utf-8")
    book = extract(tmp_path / "book.fb2")
    assert [c["title"] for c in book["chapters"]] == ["Проба", "Глава первая", "Глава вторая"]
    last = book["blocks"][-1]
    assert last["text"] == "Текст." and last["notes"] == [{"pos": 5, "id": "c7", "m": "7"}]
    assert book["notes"]["c7"] == "Комментарий."
    assert "{7}" not in "".join(b["text"] for b in book["blocks"])


def test_fb2_inline_pictures_keep_their_place(tmp_path):
    p = (
        '<p><strong>&lt;№2&gt;</strong> <image l:href="#g1.png"/>  <image l:href="#g2.png"/> <strong>Кунь.</strong>'
        ' Знак <image l:href="#g1.png"/> земли. Ещё.</p>'
    )
    plain = FB2.replace(
        "<p>Текст.</p>", p.replace('<image l:href="#g1.png"/>', "").replace('<image l:href="#g2.png"/>', "")
    )
    fb2 = FB2.replace("</FictionBook>", bins("g1.png", "g2.png") + "</FictionBook>")
    (tmp_path / "a.fb2").write_text(fb2.replace("<p>Текст.</p>", p), encoding="utf-8")
    (tmp_path / "b.fb2").write_text(plain, encoding="utf-8")
    book, without = extract(tmp_path / "a.fb2"), extract(tmp_path / "b.fb2")
    blk = book["blocks"][-1]
    assert blk["text"] == without["blocks"][-1]["text"] == "<№2> Кунь. Знак земли. Ещё."
    assert blk["sentences"] == without["blocks"][-1]["sentences"]
    assert blk["pics"] == [
        {"pos": 5, "src": "images/g1.png"},
        {"pos": 5, "src": "images/g2.png"},
        {"pos": 16, "src": "images/g1.png"},
    ]
    assert list(blk)[-2:] == ["audio", "pics"] and "pics" not in without["blocks"][-1]


def test_fb2_picture_alone_in_a_paragraph_goes_to_the_next_block(tmp_path):
    fb2 = FB2.replace("<p>Текст.</p>", '<p> <image l:href="#big.png"/> </p><p>Текст.</p>')
    fb2 = fb2.replace("</FictionBook>", bins("big.png") + "</FictionBook>")
    (tmp_path / "book.fb2").write_text(fb2, encoding="utf-8")
    book = extract(tmp_path / "book.fb2")
    assert book["blocks"][-1]["text"] == "Текст."
    assert book["blocks"][-1]["images"] == [{"src": "images/big.png"}]
    assert "pics" not in book["blocks"][-1]


# ---- completeness and robustness (audit 2026-10-07, F01-F18)

HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">'
    "<description><title-info><book-title>Проба</book-title></title-info></description>"
)


def read(tmp_path, body: str, more: str = "", head: str = HEAD, name: str = "book.fb2") -> dict:
    (tmp_path / name).write_text(f"{head}<body>{body}</body>{more}</FictionBook>", encoding="utf-8")
    return extract(tmp_path / name)


def sec(*parts: str, title: str = "Глава") -> str:
    return f"<section><title><p>{title}</p></title>{''.join(parts)}</section>"


def test_poem_subtitle_epigraph_date_and_stanza_title(tmp_path):
    """F01, F02: nothing of a poem is lost, and none of its titles is a chapter heading."""
    book = read(
        tmp_path,
        sec(
            "<poem><title><p>Песня</p></title><epigraph><p>Пой.</p><text-author>Он</text-author></epigraph>"
            "<subtitle>Первая часть</subtitle><stanza><title><p>I</p></title><subtitle>тихо</subtitle>"
            "<v>Строка.</v></stanza><date>1901</date></poem>"
        ),
    )
    got = [(b["kind"], b["text"]) for b in book["blocks"][1:]]
    assert got == [
        ("subtitle", "Песня"),
        ("epigraph", "Пой."),
        ("author", "Он"),
        ("subtitle", "Первая часть"),
        ("subtitle", "I"),
        ("subtitle", "тихо"),
        ("verse", "Строка."),
        ("date", "1901"),
    ]
    assert len(book["chapters"]) == 1


def test_rich_notes_keep_poems_pictures_and_marks(tmp_path):
    """F03, F04: a note keeps its paragraphs, verse, pictures and marks; a plain one stays a string."""
    notes = (
        '<body name="notes"><section id="n1"><title><p>1</p></title><p>Вот <emphasis>эти</emphasis> стихи:'
        ' <image l:href="#s.png"/>.</p><poem><stanza><v>Раз строка,</v><v>два строка.</v></stanza></poem>'
        '<image l:href="#t.png"/><p>Конец <strong>сноски</strong> x<sup>2</sup>.</p></section>'
        '<section id="n2"><p>Простая   сноска.</p></section></body>' + bins("s.png", "t.png")
    )
    book = read(
        tmp_path, sec('<p>Текст<a l:href="#n1" type="note">1</a> и<a l:href="#n2" type="note">2</a>.</p>'), notes
    )
    n1 = book["notes"]["n1"]
    assert n1["text"] == "Вот эти стихи: .\n\nРаз строка,\nдва строка.\n\nКонец сноски x2."
    t = n1["text"]
    assert [t[a:b] for a, b in n1["em"]] == ["эти"]
    assert [t[a:b] for a, b in n1["strong"]] == ["сноски"]
    assert [t[a:b] for a, b in n1["sup"]] == ["2"]
    assert n1["pics"] == [{"pos": 15, "src": "images/s.png"}, {"pos": t.index("Конец"), "src": "images/t.png"}]
    assert n1["kinds"] == [[t.index("Раз"), t.index("два строка.") + 11, "verse"]]
    assert book["notes"]["n2"] == "Простая сноска."


def test_space_around_removed_note_links(tmp_path):
    """F05, F06: `Кит {1}.` -> `Кит.`; `организме[69]не` -> `организме не`."""
    notes = '<body name="notes"><section id="n1"><p>Сноска.</p></section></body>'
    book = read(
        tmp_path,
        sec(
            '<p>Кит <a l:href="#n1" type="note">{1}</a>. Он плыл.</p>',
            '<p>В организме<a l:href="#n1" type="note">[69]</a>не было <emphasis>воды</emphasis>.</p>',
            '<p>Конец абзаца <a l:href="#n1" type="note">{1}</a></p>',
        ),
        notes,
    )
    a, b, c = book["blocks"][1:]
    assert a["text"] == "Кит. Он плыл." and a["notes"] == [{"pos": 3, "id": "n1", "m": "1"}]
    assert b["text"] == "В организме не было воды." and b["notes"] == [{"pos": 11, "id": "n1", "m": "69"}]
    assert [b["text"][x:y] for x, y in b["em"]] == ["воды"]
    assert c["text"] == "Конец абзаца" and c["notes"][0]["pos"] == 12


def test_sup_sub_strong_ranges(tmp_path):
    """F07, F08: superscripts, subscripts and bold are ranges, not flattened text."""
    book = read(tmp_path, sec("<p>Вода H<sub>2</sub>O, 4<sup>3</sup> и <strong>твёрдо</strong><b> так</b>.</p>"))
    b = book["blocks"][1]
    t = b["text"]
    assert t == "Вода H2O, 43 и твёрдо так."
    assert [t[x:y] for x, y in b["sub"]] == ["2"]
    assert [t[x:y] for x, y in b["sup"]] == ["3"]
    assert [t[x:y] for x, y in b["strong"]] == ["твёрдо так"]


def test_table_is_one_block_with_rows(tmp_path):
    """F09: a table is kept: cells by tabs, rows by line breaks, header cells flagged, not narrated."""
    notes = '<body name="notes"><section id="n1"><p>Сноска.</p></section></body>'
    table = (
        "<table><tr><th>Имя</th><th>Число</th></tr><tr><td><emphasis>Лёд</emphasis></td>"
        '<td>4<a l:href="#n1" type="note">1</a></td></tr></table>'
    )
    b = read(tmp_path, sec("<p>Перед таблицей.</p>", table), notes)["blocks"][2]
    assert b["kind"] == "table" and b["text"] == "Имя\tЧисло\nЛёд\t4" and b["audio"] is False
    assert b["rows"] == [[[0, 3, 1], [4, 9, 1]], [[10, 13], [14, 15]]]
    assert b["sentences"] == [[0, 9], [10, 15]]
    assert b["em"] == [[10, 13]] and b["notes"] == [{"pos": 15, "id": "n1", "m": "1"}]


def test_images_never_cross_a_chapter_end(tmp_path):
    """F10: an image at a chapter's end trails its last block; the book's last image is kept."""
    book = read(
        tmp_path,
        sec('<p>Раз.</p><image l:href="#a.png"/>', title="Первая")
        + sec('<image l:href="#b.png"/><p>Два.</p><image l:href="#c.png"/>', title="Вторая"),
        bins("a.png", "b.png", "c.png"),
    )
    imgs = [(b["text"], b["images"]) for b in book["blocks"]]
    assert imgs == [
        ("Первая", []),
        ("Раз.", [{"src": "images/a.png", "after": True}]),
        ("Вторая", []),
        ("Два.", [{"src": "images/b.png"}, {"src": "images/c.png", "after": True}]),
    ]


def test_untitled_sections(tmp_path):
    """F11: a nested section without a title is no chapter; a top-level one is, named by a heading-like line."""
    book = read(
        tmp_path,
        "<section><p>2-е издание</p><p>Текст введения.</p></section>"
        + sec("<p>Раз.</p><section><p>Два.</p></section><section><p>Три.</p></section>", title="Глава"),
    )
    assert [(c["title"], c["level"]) for c in book["chapters"]] == [("2-е издание", 1), ("Глава", 1)]
    assert [b["chapter"] for b in book["blocks"]] == [0, 0, 1, 1, 1, 1]
    untitled = read(tmp_path, "<section><p>Длинный первый абзац, который явно не заголовок.</p></section>")
    assert untitled["chapters"][0]["title"] == ""


def test_title_paragraphs_join_as_a_phrase(tmp_path):
    """F12: `Глава 1. Странствия`, but `Часть первая: Начало` and `Глава вторая, в которой` stay as they are."""
    book = read(
        tmp_path,
        '<section><title><p>Глава 1</p><p>Странствия<a l:href="#n1" type="note">{2}</a></p></title><p>Раз.</p>'
        "<section><title><p>Часть первая:</p><empty-line/><p>Начало</p></title><p>Два.</p></section>"
        "<section><title><p>Глава вторая,</p><p>в которой всё</p></title><p>Три.</p></section></section>",
        '<body name="notes"><section id="n1"><p>Сноска.</p></section></body>',
    )
    assert [c["title"] for c in book["chapters"]] == [
        "Глава 1. Странствия",
        "Часть первая: Начало",
        "Глава вторая, в которой всё",
    ]


def test_asterisk_footnotes(tmp_path):
    """F14: `* text` after a block with `word*` is that word's note; `* * *` is a scene break."""
    book = read(
        tmp_path,
        sec(
            "<p>Это МЕХАНИЗМ* мира и ВЕЩЬ** тоже.</p>",
            "<p>* Мировоззрение (<emphasis>нем.</emphasis>).</p>",
            "<p>** Вторая сноска.</p>",
            "<p>* * *</p>",
            "<p>* Без маркера выше.</p>",
        ),
    )
    texts = [b["text"] for b in book["blocks"]]
    assert texts == ["Глава", "Это МЕХАНИЗМ мира и ВЕЩЬ тоже.", "* * *", "* Без маркера выше."]
    b = book["blocks"][1]
    assert b["notes"] == [{"pos": 12, "id": "star1", "m": "*"}, {"pos": 24, "id": "star2", "m": "**"}]
    assert b["sentences"] == [[0, len(b["text"])]]
    assert book["notes"] == {"star1": {"text": "Мировоззрение (нем.).", "em": [[15, 19]]}, "star2": "Вторая сноска."}


def test_indent_kept_only_for_verse(tmp_path):
    """F16: leading no-break spaces stay on a verse line only."""
    book = read(
        tmp_path,
        sec(
            "<p>\xa0\xa0 1. Даос.</p><cite><p>\xa0\xa0Цитата.</p></cite><poem><stanza><v>\xa0\xa0Стих.</v></stanza></poem>"
        ),
    )
    assert [b["text"] for b in book["blocks"][1:]] == ["1. Даос.", "Цитата.", "  Стих."]


def test_any_later_body_can_hold_notes(tmp_path):
    """F18: a later body named otherwise, or unnamed but linked, holds notes; an untyped link into it is a note."""
    book = read(
        tmp_path,
        sec('<p>Раз<a l:href="#x1">[1]</a> и два<a l:href="#y1">*</a>.</p>'),
        '<body name="Примечания"><section id="x1"><p>Первая.</p></section></body>'
        '<body><section id="y1"><p>Вторая.</p></section></body>',
    )
    b = book["blocks"][1]
    assert b["text"] == "Раз и два." and [(n["id"], n["m"]) for n in b["notes"]] == [("x1", "1"), ("y1", "*")]
    assert book["notes"] == {"x1": "Первая.", "y1": "Вторая."}
    assert len(book["blocks"]) == 2


def test_html_entities_and_stray_ampersands(tmp_path):
    book = read(tmp_path, sec("<p>&laquo;Он&raquo;&nbsp;пришёл &mdash; Smith & Co &amp; &unknown; &#1046;.</p>"))
    assert book["blocks"][1]["text"] == "«Он» пришёл — Smith & Co & &unknown; Ж."


def test_any_namespace_or_none(tmp_path):
    body = sec('<p>Текст<a xlink:href="#n1" type="note">1</a>.</p>')
    notes = '<body name="notes"><section id="n1"><p>Сноска.</p></section></body>'
    for i, head in enumerate(
        [
            HEAD.replace("fictionbook/2.0", "fictionbook/2.1").replace("xmlns:l=", "xmlns:xlink="),
            HEAD.replace(' xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"', "").replace(
                ' xmlns:l="http://www.w3.org/1999/xlink"', ""
            ),
        ]
    ):
        book = read(tmp_path, body, notes, head=head, name=f"b{i}.fb2")
        assert book["blocks"][1]["text"] == "Текст." and book["notes"] == {"n1": "Сноска."}, i


def test_bad_base64_drops_only_that_picture(tmp_path):
    import base64

    good = base64.b64encode(b"picture").decode()
    book = read(
        tmp_path,
        sec('<image l:href="#bad.png"/><image l:href="#good.png"/><p>Текст.</p>'),
        f'<binary id="bad.png" content-type="image/png">!!!не base64</binary>'
        f'<binary id="good.png" content-type="image/png">{good}</binary>',
    )
    assert book["blocks"][1]["text"] == "Текст."
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == ["good.png"]


def test_encoding_from_the_declaration(tmp_path):
    xml = HEAD.replace("utf-8", "windows-1251") + "<body>" + sec("<p>Привет&nbsp;мир.</p>") + "</body></FictionBook>"
    (tmp_path / "w.fb2").write_bytes(xml.encode("cp1251"))
    book = extract(tmp_path / "w.fb2")
    assert book["blocks"][1]["text"] == "Привет мир."


def test_a_picture_not_written_is_referenced_nowhere(tmp_path):
    """A binary with broken base64, or none at all, leaves no picture behind: not in `images`, not in `pics`, not
    in a note; a paragraph holding only such a picture adds nothing."""
    notes = (
        '<body name="notes"><section id="n1"><p>Знак <image l:href="#bad.png"/> и <image l:href="#ok.png"/>.</p>'
        '<image l:href="#none.png"/></section></body>'
    )
    book = read(
        tmp_path,
        sec(
            '<image l:href="#bad.png"/><p>Раз <image l:href="#none.png"/>знак<a l:href="#n1" type="note">1</a>.</p>',
            '<p><image l:href="#bad.png"/></p><p>Два <image l:href="#ok.png"/>.</p><image l:href="#none.png"/>',
        ),
        notes + bins("ok.png") + '<binary id="bad.png" content-type="image/png">!!!не base64</binary>',
    )
    assert [(b["text"], b["images"], b.get("pics")) for b in book["blocks"][1:]] == [
        ("Раз знак.", [], None),
        ("Два .", [], [{"pos": 4, "src": "images/ok.png"}]),
    ]
    assert book["notes"]["n1"] == {"text": "Знак и .", "pics": [{"pos": 7, "src": "images/ok.png"}]}
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == ["ok.png"]


def test_a_named_appendix_body_is_text(tmp_path):
    """A later body named otherwise and not linked as notes (`Приложение`) is read after the main text, its title a
    chapter, though one of its three sections is linked; a body named footnotes holds notes."""
    book = read(
        tmp_path,
        sec('<p>Раз<a l:href="#n1">[1]</a> и <a l:href="#app1">см. приложение</a>.</p>'),
        '<body name="Приложение"><title><p>Приложение</p></title><section id="app1"><title><p>Даты</p></title>'
        '<p>Первая дата.</p></section><section id="app2"><p>Вторая дата.</p></section>'
        '<section id="app3"><p>Третья дата.</p></section></body>'
        '<body name="Комментарии"><section id="n1"><p>Сноска.</p></section></body>'
        '<body name="FootNotes"><section id="f1"><p>Никто не ссылается.</p></section></body>',
    )
    assert [c["title"] for c in book["chapters"]] == ["Глава", "Приложение", "Даты", "", ""]
    assert [b["text"] for b in book["blocks"]][-4:] == ["Даты", "Первая дата.", "Вторая дата.", "Третья дата."]
    assert book["notes"] == {"n1": "Сноска.", "f1": "Никто не ссылается."}
    assert book["blocks"][1]["text"] == "Раз и см. приложение."


NOTES = '<body name="notes"><section id="n1"><p>Сноска.</p></section><section id="n2"><p>Вторая.</p></section></body>'


def test_a_quote_after_a_note_link_closes_only_before_a_space_or_punctuation(tmp_path):
    """X7: `сказал {1}"Привет"` keeps its space (the quote opens); `слово {1}".` loses it (the quote closes)."""
    book = read(
        tmp_path,
        sec(
            '<p>Он сказал <a l:href="#n1" type="note">1</a>"Привет" и ушёл.</p>',
            '<p>Слово <a l:href="#n1" type="note">1</a>". Дальше.</p>',
            '<p>Конец <a l:href="#n2">2</a>\'</p>',
        ),
        NOTES,
    )
    a, b, c = book["blocks"][1:]
    assert a["text"] == 'Он сказал "Привет" и ушёл.' and a["notes"][0]["pos"] == 9
    assert b["text"] == 'Слово". Дальше.' and b["notes"][0]["pos"] == 5
    assert c["text"] == "Конец'" and c["notes"][0]["pos"] == 5


def test_a_note_anchor_between_spaces_follows_the_word_before_it(tmp_path):
    """X8: `Слово {1} следующее` -> the anchor right after «Слово», as the HTML reading puts it."""
    book = read(
        tmp_path,
        sec(
            '<p>Слово <a l:href="#n1" type="note">{1}</a> следующее.</p>',
            '<p>Слово <a l:href="#n1" type="note">1</a>(скобка).</p>',
        ),
        NOTES,
    )
    a, b = book["blocks"][1:]
    assert a["text"] == "Слово следующее." and a["notes"] == [{"pos": 5, "id": "n1", "m": "1"}]
    assert b["text"] == "Слово (скобка)." and b["notes"][0]["pos"] == 5


def test_an_undefined_byte_costs_a_character_not_the_book(tmp_path):
    """X9: windows-1251 with a byte it does not define (0x98) is still read as windows-1251."""
    xml = HEAD.replace("utf-8", "windows-1251") + "<body>" + sec("<p>Привет мир.</p>") + "</body></FictionBook>"
    (tmp_path / "w.fb2").write_bytes(
        xml.encode("cp1251").replace("мир".encode("cp1251"), b"\x98" + "мир".encode("cp1251"))
    )
    assert extract(tmp_path / "w.fb2")["blocks"][1]["text"] == "Привет �мир."


def test_blank_lines_before_the_declaration_and_bad_character_references(tmp_path):
    """X10: a BOM and blank lines before `<?xml` are dropped, `&#1;` and other characters XML has not go."""
    xml = HEAD.replace("utf-8", "x-cp1251") + "<body>" + sec("<p>При&#1;вет&#xFFFE; &#99999999999;мир &#1046;.</p>")
    (tmp_path / "w.fb2").write_bytes(b"\xef\xbb\xbf\r\n  " + (xml + "</body></FictionBook>").encode("cp1251"))
    assert extract(tmp_path / "w.fb2")["blocks"][1]["text"] == "Привет мир Ж."


def test_a_zip_without_an_fb2_says_so(tmp_path):
    import zipfile

    import pytest

    with zipfile.ZipFile(tmp_path / "book.fb2.zip", "w") as z:
        z.writestr("readme.txt", "нет")
    with pytest.raises(SystemExit, match="В архиве нет книги"):
        extract(tmp_path / "book.fb2.zip")


def test_text_after_a_nested_chapter_is_that_chapters(tmp_path):
    """X11: chapters stay in block order: text after a nested titled section goes with it, as the reader shows it."""
    book = read(
        tmp_path,
        sec("<p>Раз.</p>", sec("<p>Два.</p>", title="Вложенная"), "<p>Три.</p>", title="Родитель")
        + sec("<p>Четыре.</p>"),
    )
    assert [(c["title"], c["level"], c["first_block"]) for c in book["chapters"]] == [
        ("Родитель", 1, 0),
        ("Вложенная", 2, 2),
        ("Глава", 1, 5),
    ]
    assert [b["chapter"] for b in book["blocks"]] == [0, 0, 1, 1, 1, 2, 2]


def test_a_body_without_sections_has_one_untitled_chapter(tmp_path):
    """X12: as TXT and HTML: chapter s0, and text before the first section is its own untitled chapter."""
    book = read(tmp_path, "<p>Просто текст.</p><p>Ещё.</p>")
    assert book["chapters"] == [{"id": "s0", "title": "", "level": 1, "first_block": 0}]
    book = read(tmp_path, "<epigraph><p>Эпиграф.</p></epigraph>" + sec("<p>Раз.</p>"))
    assert [(c["title"], c["first_block"]) for c in book["chapters"]] == [("", 0), ("Глава", 1)]
    assert [b["chapter"] for b in book["blocks"]] == [0, 1, 1]


def test_a_link_to_a_missing_note_keeps_its_text(tmp_path):
    """X13."""
    b = read(tmp_path, sec('<p>Текст<a l:href="#nope" type="note">[5]</a> и<a l:href="#n1">1</a>.</p>'), NOTES)
    assert b["blocks"][1]["text"] == "Текст[5] и." and [n["id"] for n in b["blocks"][1]["notes"]] == ["n1"]


def test_an_asterisk_between_digits_is_no_marker(tmp_path):
    """X14: `5*3` is arithmetic; `МЕХАНИЗМ*,` a marker."""
    book = read(tmp_path, sec("<p>Итого 5*3 = 15, а МЕХАНИЗМ*, x*y.</p>", "<p>* Пункт.</p>"))
    b = book["blocks"][1]
    assert b["text"] == "Итого 5*3 = 15, а МЕХАНИЗМ, x*y." and b["notes"] == [{"pos": 26, "id": "star1", "m": "*"}]
    assert book["notes"]["star1"] == "Пункт."


def test_note_links_inside_an_asterisk_note_keep_their_text(tmp_path):
    """X15: one level of notes: a link in a `* ...` note stays as its text."""
    book = read(
        tmp_path,
        sec(
            "<p>Это МЕХАНИЗМ* мира.</p>",
            '<p>* Пояснение<a l:href="#n1" type="note">[1]</a> и <emphasis>это</emphasis>.</p>',
        ),
        NOTES,
    )
    assert book["notes"]["star1"] == {"text": "Пояснение[1] и это.", "em": [[15, 18]]}


def test_a_paragraph_of_only_a_note_link_keeps_its_anchor(tmp_path):
    """X23: the anchor goes to the end of the previous block, or to the start of the next when there is none."""
    book = read(
        tmp_path,
        '<p><a l:href="#n2" type="note">2</a></p>'
        + sec("<p>Первый абзац.</p>", '<p> <a l:href="#n1" type="note">1</a> </p>', "<p>Ещё.</p>"),
        NOTES,
    )
    title, first, more = book["blocks"]
    assert title["notes"] == [{"pos": 0, "id": "n2", "m": "2"}]
    assert first["notes"] == [{"pos": 13, "id": "n1", "m": "1"}] and more["notes"] == []


def test_block_ids_never_repeat(tmp_path):
    """X49: a made-up id is no id of the file's; an id the file repeats gets a suffix."""
    book = read(tmp_path, sec('<p id="b3">Раз.</p>', "<p>Два.</p>", '<p id="b3">Три.</p>', "<p>Четыре.</p>"))
    assert [b["id"] for b in book["blocks"]] == ["b0", "b3", "b2", "b3-2", "b4"]


def test_deep_nesting_is_read_without_recursion(tmp_path):
    """W2: 3000 nested emphasis or citations: the text is kept, as one paragraph past `DEPTH` levels."""
    book = read(tmp_path, sec("<p>" + "<emphasis>" * 3000 + "Текст." + "</emphasis>" * 3000 + " хвост</p>"))
    assert book["blocks"][1]["text"] == "Текст. хвост" and book["blocks"][1]["em"] == [[0, 6]]
    book = read(tmp_path, sec("<cite>" * 3000 + "<p>Цитата.</p><p>Вторая.</p>" + "</cite>" * 3000 + "<p>После.</p>"))
    assert [(b["kind"], b["text"]) for b in book["blocks"][1:]] == [("cite", "Цитата.Вторая."), ("p", "После.")]


def test_html5_entities_become_their_characters(tmp_path):
    """W8: any HTML5 name, and an old one without its semicolon read as Python's html reads it."""
    book = read(tmp_path, sec("<p>&alpha; &hearts; &frac13; &NotEqualTilde; &notit; &ampx; &Unknown;.</p>"))
    assert book["blocks"][1]["text"] == "α ♥ ⅓ ≂̸ ¬it; &x; &Unknown;."


def test_the_phone_has_pythons_entities_and_encodings():
    """W8, W23: ios/Sources/Import/FB2.swift carries html.entities.html5 and extract_fb2.ENCODINGS as they are."""
    import html.entities
    import re

    import extract_fb2

    swift = (Path(__file__).resolve().parent.parent / "ios/Sources/Import/FB2.swift").read_text(encoding="utf-8")
    table = swift.split('entityTable = """', 1)[1].split('"""', 1)[0].replace("\\\n", "").split()
    got = {k: "".join(chr(int(x, 16)) for x in v.split(",")) for k, v in (e.split("=", 1) for e in table)}
    assert got == html.entities.html5
    enc = swift.split("static let encodings: [String: String] = [", 1)[1].split("]", 1)[0]
    assert dict(re.findall(r'"([^"]+)": "([^"]+)"', enc)) == extract_fb2.ENCODINGS


def test_encoding_names_both_decoders_read(tmp_path):
    """W23: names Apple knows and Python's codecs do not (koi8r, x-cp1251, x-mac-cyrillic) read the same."""
    for name, codec in (("koi8r", "koi8_r"), ("x-cp1251", "cp1251"), ("x-mac-cyrillic", "mac_cyrillic")):
        xml = HEAD.replace("utf-8", name) + "<body>" + sec("<p>Привет, мир.</p>") + "</body></FictionBook>"
        (tmp_path / f"{name}.fb2").write_bytes(xml.encode(codec))
        assert extract(tmp_path / f"{name}.fb2")["blocks"][1]["text"] == "Привет, мир.", name


def test_binary_ids_name_pictures_as_links_do(tmp_path):
    """W17: `dir/pic.png` is written as images/pic.png and linked so."""
    book = read(tmp_path, sec('<image l:href="#dir/pic.png"/><p>Текст.</p>'), bins("dir/pic.png"))
    assert book["blocks"][1]["images"] == [{"src": "images/pic.png"}]
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == ["pic.png"]


def test_lines_after_a_nested_stanza_keep_their_stanza(tmp_path):
    """W18."""
    book = read(tmp_path, sec("<poem><stanza><v>Раз</v><stanza><v>Два</v></stanza><v>Три</v></stanza></poem>"))
    assert [(b["text"], b["stanza"]) for b in book["blocks"][1:]] == [("Раз", 1), ("Два", 2), ("Три", 1)]


def test_title_info_from_any_description(tmp_path):
    """W20: the first description holding a title-info."""
    head = HEAD.replace(
        "<description>", "<description><document-info><id>x</id></document-info></description><description>"
    )
    assert read(tmp_path, sec("<p>Текст.</p>"), head=head)["title"] == "Проба"
