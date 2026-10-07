#!/usr/bin/env python3
"""Shared import vectors: small made-up books in every format the iPhone app imports, and what the Python
pipeline makes of them. The phone's importer (ios/Sources/Import) is tested against these files, so the
two implementations do not drift apart unnoticed.

  .venv/bin/python tests/make_import_vectors.py           # (re)write tests/import_vectors/
  .venv/bin/python tests/make_import_vectors.py --check   # exit 1 when the vectors are stale

For each source file `<name>` the folder holds `<name>.book.json`, the book.json exactly as the pipeline
writes it, and `expected.json` lists every case: the images (sha256), the book.toml values that come from
the text (title, author, text_end, fragment_note) or the error a stub or an archive without a book stops
at. `sentences.json` is the sentence splitter on hard cases, `model.book.json` every optional key of the
book.json model as dump_book writes it, `txt.json` small plain texts (a rule each) and their book.json, `pdf.json`
pages of PDF lines (a rule each) and the book.json made of them, `epub.json` small epubs (a rule each) and the
book.json and pictures extract_epub makes of them, or the error it stops at, `markup.json` bits of XHTML and the
tree extract_epub.parse (html.parser, BeautifulSoup) makes of each.
Everything is deterministic: running the script twice changes
nothing. All text is made up.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import add_book  # noqa: E402
import extract_epub  # noqa: E402
import extract_fb2  # noqa: E402
import extract_pdf  # noqa: E402
import extract_txt  # noqa: E402
from extract_text import dump_book, split_sentences  # noqa: E402
from merge_books import merge  # noqa: E402
from text_end import text_end  # noqa: E402

OUT = ROOT / "tests" / "import_vectors"
EXTRACT = {
    "fb2": extract_fb2.extract,
    "fb2zip": extract_fb2.extract,
    "epub": extract_epub.extract,
    "pdf": extract_pdf.extract,
    "txt": extract_txt.extract,
}
EXT = {"fb2": ".fb2", "fb2zip": ".fb2.zip", "epub": ".epub", "pdf": ".pdf", "txt": ".txt"}
ZIP_TIME = (2020, 1, 1, 0, 0, 0)

# a 1x1 PNG and a tiny JPEG-looking file: the importer copies images, it never decodes them
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


# ---------------------------------------------------------------- made-up prose


class Lcg:
    """A tiny deterministic generator: the same text on every Python version."""

    def __init__(self, seed: int) -> None:
        self.s = seed

    def pick(self, items: list[str]) -> str:
        self.s = (self.s * 1103515245 + 12345) % 2**31
        return items[(self.s >> 8) % len(items)]

    def num(self, n: int) -> int:
        self.s = (self.s * 1103515245 + 12345) % 2**31
        return (self.s >> 8) % n


NOUNS = [
    "ветер",
    "река",
    "дорога",
    "старый дом",
    "мост",
    "сад",
    "берег",
    "лодка",
    "снег",
    "свет в окне",
    "письмо",
    "город",
    "лес",
    "колокол",
    "голос",
    "туман",
    "маяк",
    "сторож",
]
VERBS = ["шумел", "ждал", "молчал", "светился", "тянулся", "звенел", "дрожал", "остывал", "темнел", "пел"]
ADVS = [
    "долго",
    "тихо",
    "весь вечер",
    "до утра",
    "за холмом",
    "у самой воды",
    "под дождём",
    "как прежде",
    "едва слышно",
]
WHO = ["сторож", "Анна", "капитан", "мальчик", "старик"]


def sentence(r: Lcg) -> str:
    n, v, a = r.pick(NOUNS), r.pick(VERBS), r.pick(ADVS)
    k = r.num(9)
    if k == 0:
        return f"— {n.capitalize()} {v} {a}, — сказал {r.pick(WHO)}."
    if k == 1:
        return f"В {1800 + r.num(200)} г. здесь {v} {n}."
    if k == 2:
        return f"«{n.capitalize()} {v}?» — спросила {r.pick(['Анна', 'Вера'])}."
    if k == 3:
        return f"Было {r.num(12) + 1} часов, и {n} {v} {a}!"
    if k == 4:
        return f"{n.capitalize()} {v}, т. е. {r.pick(VERBS)} {a}…"
    if k == 5:
        return f"{n.capitalize()} {v} {a}; потом {r.pick(NOUNS)} {r.pick(VERBS)}."
    return f"{n.capitalize()} {v} {a}."


def paragraph(r: Lcg) -> str:
    return " ".join(sentence(r) for _ in range(2 + r.num(4)))


def prose(seed: int, count: int) -> list[str]:
    r = Lcg(seed)
    return [paragraph(r) for _ in range(count)]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- sources


def fb2_povest() -> bytes:
    p = prose(1, 40)
    body = [
        "<title><p>Пётр Выдумкин</p><p>Повесть о маяке</p></title>",
        "<epigraph><p>Свет виден издалека.</p><text-author>Неизвестный автор</text-author></epigraph>",
        '<section id="ch1"><title><p>Глава первая</p><p>Берег</p></title>',
        '<p id="p-first">Маяк стоял на мысу\xa0— один,   как\n  палец. Сторож <emphasis>зажигал</emphasis>'
        '<emphasis> огонь</emphasis> каждый вечер<a l:href="#n1" type="note">[1]</a>, и <strong>все</strong>'
        " корабли <emphasis>  видели   его  </emphasis>. Это было в 1901 г. Тогда ещё никто не знал, т. е. не"
        " догадывался… «Кто там?» — спросила Анна.</p>",
        '<image l:href="#map.png"/>',
        *[f"<p>{esc(x)}</p>" for x in p[:12]],
        "<poem><title><p>Песня сторожа</p></title>",
        "<stanza><v>Огонь горит над морем,</v><v>   и волны спят внизу.</v></stanza>",
        "<stanza><v>Я жду тебя, я помню,</v><v>я лодку привезу.</v></stanza>",
        "<text-author>Сторож</text-author></poem>",
        "<subtitle>* * *</subtitle>",
        "<cite><p>Свет — это обещание берега.</p><text-author>Из вахтенного журнала</text-author></cite>",
        "<empty-line/>",
        '<section id="ch1-2"><title><p>Ночь</p></title>',
        *[f"<p>{esc(x)}</p>" for x in p[12:20]],
        '<p>Вторая сноска здесь<a l:href="#n2" type="note">2</a>.</p>',
        "</section></section>",
        '<section id="ch2"><title><p>Глава вторая</p></title>',
        *[f"<p>{esc(x)}</p>" for x in p[20:38]],
        "</section>",
        '<section id="about"><title><p>Об авторе</p></title>',
        *[f"<p>{esc(x)}</p>" for x in p[38:40]],
        "</section>",
    ]
    notes = (
        '<body name="notes"><title><p>Примечания</p></title>'
        '<section id="n1"><title><p>1</p></title><p>Первая   сноска.</p></section>'
        '<section id="n2"><p>Вторая сноска,</p><p>в двух абзацах.</p></section></body>'
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><title-info><genre>prose</genre><author><first-name>Пётр</first-name>"
        "<last-name>Выдумкин</last-name></author><book-title>Повесть о маяке</book-title>"
        '<coverpage><image l:href="#cover.jpg"/></coverpage><lang>ru</lang></title-info></description>\n'
        "<body>\n" + "\n".join(body) + "\n</body>\n" + notes + "\n"
        f'<binary id="cover.jpg" content-type="image/jpeg">{base64.b64encode(JPG).decode()}</binary>\n'
        f'<binary id="map.png" content-type="image/png">\n{base64.encodebytes(PNG).decode()}</binary>\n'
        "</FictionBook>\n"
    )
    return xml.encode("utf-8")


def fb2_1251() -> bytes:
    p = prose(2, 36)
    secs = []
    for i, title in enumerate(["Первый день", "Второй день", "Третий день"]):
        paras = "".join(f"<p>{esc(x)}</p>\n" for x in p[i * 12 : (i + 1) * 12])
        secs.append(f"<section><title><p>{title}</p></title>\n{paras}</section>")
    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><title-info><author><first-name>Вера</first-name><middle-name>Н.</middle-name>"
        "<last-name>Придумова</last-name></author><book-title>Рассказ в три дня</book-title><lang>ru</lang>"
        "</title-info></description>\n<body>\n" + "\n".join(secs) + "\n</body>\n</FictionBook>\n"
    )
    return xml.encode("cp1251")


class Unseekable(io.RawIOBase):
    """A stream zipfile cannot seek back in: it writes data descriptors, as streaming zippers do."""

    def __init__(self) -> None:
        self.buf = io.BytesIO()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        return self.buf.write(b)

    def seekable(self) -> bool:
        return False


def zipped(files: list[tuple[str, bytes, int]], stream: bool = False) -> bytes:
    sink = Unseekable() if stream else io.BytesIO()
    with zipfile.ZipFile(sink, "w") as z:
        for name, data, method in files:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = method
            z.writestr(info, data)
    return sink.buf.getvalue() if stream else sink.getvalue()


def fb2_sbornik() -> bytes:
    p = prose(3, 30)
    poem = (
        "<poem><stanza><v>Над рекою туман,</v><v>над туманом луна.</v></stanza>"
        "<stanza><v>Кто не спит — тот и прав,</v><v>и дорога видна.</v></stanza></poem>"
    )
    body = (
        '<section><title><p>Часть первая</p></title><section id="a"><title><p>Туман</p></title>'
        + poem
        + "".join(f"<p>{esc(x)}</p>" for x in p[:15])
        + '</section></section><section><title><p>Часть вторая</p></title><section id="b"><title><p>Луна</p></title>'
        + "".join(f"<p>{esc(x)}</p>" for x in p[15:])
        + "</section></section>"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:xlink="http://www.w3.org/1999/xlink">'
        "<description><title-info><author><first-name>Олег</first-name><last-name>Небывалов</last-name></author>"
        "<book-title>Сборник туманов</book-title><lang>ru</lang></title-info></description>"
        f"<body>{body}</body></FictionBook>"
    )
    return zipped([("sbornik.fb2", xml.encode("utf-8"), zipfile.ZIP_DEFLATED)], stream=True)


XHTML_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
    "<head><title>{t}</title></head>\n<body>\n"
)


def epub3() -> bytes:
    p = prose(4, 36)
    ch1 = (
        XHTML_HEAD.format(t="Глава 1") + "<h1>Часть&nbsp;первая</h1>\n<h2>Глава&nbsp;1. <span>Утро</span></h2>\n"
        '<p class="epigraph">Утро начинается с&nbsp;тишины.</p>\n'
        "<p>Он сказал&nbsp;&mdash; и&nbsp;замолчал. &laquo;Пойдём?&raquo; &mdash; спросила Анна&hellip;"
        " Это было <i>давно</i>, <em>очень</em><i> давно</i>.<br/>И&nbsp;всё же.</p>\n"
        '<div class="illus"><img src="../images/fig1.png" alt=""/></div>\n'
        + "".join(f"<p>{esc(x)}</p>\n" for x in p[:14])
        + '<p class="verse">Течёт река, течёт,</p>\n<p class="verse">и время с ней течёт.</p>\n'
        "<blockquote><p>Река помнит всё.</p><p>&mdash; Старая пословица</p></blockquote>\n"
        "<h4>* * *</h4>\n" + "".join(f"<p>{esc(x)}</p>\n" for x in p[14:20]) + "</body></html>\n"
    )
    ch2 = (
        XHTML_HEAD.format(t="Глава 2")
        + "<section><h2>Глава 2</h2>\n<h3>Вечер</h3>\n"
        + "".join(f"<p>{esc(x)}</p>\n" for x in p[20:])
        + "<table><tr><td>не текст</td></tr></table>\n</section></body></html>\n"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
        ' unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000001</dc:identifier>'
        "<dc:title>Книга   реки</dc:title><dc:creator>Анна Сочинская</dc:creator><dc:language>ru</dc:language>"
        "</metadata><manifest>"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="fig1" href="images/fig1.png" media-type="image/png"/>'
        '<item id="cover" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
        '</manifest><spine><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>'
    )
    nav = XHTML_HEAD.format(t="Оглавление") + "<nav epub:type='toc'><ol><li>1</li></ol></nav></body></html>"
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    return zipped(
        [
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/nav.xhtml", nav.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch1.xhtml", ch1.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch2.xhtml", ch2.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/images/fig1.png", PNG, zipfile.ZIP_STORED),
            ("OEBPS/images/cover.jpg", JPG, zipfile.ZIP_STORED),
        ]
    )


def epub2() -> bytes:
    p = prose(5, 30)
    cover = (
        "<html><head><title>Обложка</title></head><body><div>"
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<image width="600" height="800" xlink:href="cover.jpeg"/></svg></div></body></html>'
    )
    text = (
        "<html><head><title>Текст</title></head><body>\n<h2>Записка</h2>\n"
        + "".join(f"<p>{esc(x)}</p>\n" for x in p)
        + "<P CLASS='Verse'>Строка   стиха</P>\n"
        "<p>Конец ознакомительного фрагмента. Купить полную версию можно у автора.</p>\n</body></html>"
    )
    opf = (
        '<?xml version="1.0"?><package version="2.0" xmlns="http://www.idpf.org/2007/opf">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">'
        '<dc:title>Записка о\n  тумане</dc:title><dc:creator opf:role="aut">Иван Небыль</dc:creator>'
        '<dc:language>ru</dc:language><meta name="cover" content="cover-img"/></metadata><manifest>'
        '<item id="cov" href="cover.html" media-type="application/xhtml+xml"/>'
        '<item id="txt" href="text.html" media-type="application/xhtml+xml"/>'
        '<item id="cover-img" href="cover.jpeg" media-type="image/jpeg"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '</manifest><spine toc="ncx"><itemref idref="cov"/><itemref idref="txt"/></spine></package>'
    )
    container = '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
    return zipped(
        [
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container.encode(), zipfile.ZIP_DEFLATED),
            ("content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
            ("cover.html", cover.encode(), zipfile.ZIP_DEFLATED),
            ("text.html", text.encode(), zipfile.ZIP_DEFLATED),
            ("cover.jpeg", JPG, zipfile.ZIP_DEFLATED),
        ]
    )


STYLE_CSS = """@charset "utf-8";
/* the book's own look: what the reader keeps of it is book.json's `st` */
body { text-align: justify; text-indent: 1.5em; margin: 0 5% }
p { margin: 0; text-indent: 1.5em }
p.noind, .first { text-indent: 0 }
.center { text-align: center; text-indent: 0 }
.right { text-align: RIGHT !important }
div.poem p { margin-left: 9em }
.poem { margin-left: 2em; text-align: left; text-indent: 0 }
h1, h2 { text-align: center; margin: 1em 0 }
h3 { text-align: left; margin: 0 0 0 10% }
h4 { text-align: right }
.epigraph { margin-left: 40%; text-align: left }
.quote { padding-left: 24px; margin-left: 12pt }
.hidden { text-indent: -9999px }
.huge { margin-left: 500px; text-indent: 30em }
.wide { text-indent: 5em; margin-left: 15em }
@media screen { p { text-align: left } .x { color: red } }
@font-face { font-family: X; src: url(x.ttf) }
p#x, p:first-child, *, .center > p { text-align: right }
.a.b { text-align: center }
.px { margin: 0 0 0 8px; padding: 0 0 0 0.25em }
"""


def epub_stil() -> bytes:
    """An epub that styles its text: a stylesheet, a <style> element, inline styles, empty paragraphs."""
    p = prose(9, 40)
    head = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml">'
        '<head><title>{t}</title><link rel="stylesheet" type="text/css" href="../styles/book.css"/>{extra}'
        "</head>\n<body>\n"
    )
    ch1 = (
        head.format(t="1", extra="<style>.ind2 { text-indent: 2em } .blue { color: blue }</style>")
        + "<h2>Глава первая</h2>\n"
        + '<p class="epigraph">Всё начинается с реки.</p>\n'
        + f'<p class="first">{esc(p[0])}</p>\n'
        + "".join(f"<p>{esc(x)}</p>\n" for x in p[1:6])
        + '<p class="center">* * *</p>\n'
        + f'<div class="center"><p>{esc(p[6])}</p><p class="right">{esc(p[7])}</p></div>\n'
        + f'<div style="text-align:right; text-indent:0"><div><p>{esc(p[8])}</p></div></div>\n'
        + f'<p style="text-indent: 0.5em; margin-left: 1.25em">{esc(p[9])}</p>\n'
        + "<p>&nbsp;</p>\n"
        + f"<p>{esc(p[10])}</p>\n"
        + "<p></p><p> </p>\n"
        + f'<p class="ind2 blue">{esc(p[11])}</p>\n'
        + '<p class="verse poem">Течёт река, течёт,</p>\n<p class="verse poem">и время с ней течёт.</p>\n'
        + f'<blockquote class="quote"><p>{esc(p[12])}</p><p class="right">— Старая пословица</p></blockquote>\n'
        + "<h3>Часть речи</h3>\n<h4>* * *</h4>\n"
        + f'<p class="a b">{esc(p[13])}</p>\n<p class="a">{esc(p[14])}</p>\n'
        + f'<p class="hidden">{esc(p[15])}</p>\n<p class="huge">{esc(p[16])}</p>\n'
        + f'<p class="wide">{esc(p[17])}</p>\n<p class="px">{esc(p[18])}</p>\n'
        + f'<p id="x" style="text-align: middle; text-indent: -1em; margin-left: auto">{esc(p[19])}</p>\n'
        + "<p>&nbsp;</p>\n</body></html>\n"
    )
    ch2 = (
        head.format(t="2", extra="")
        + "<p>&nbsp;</p>\n<h2>Глава вторая</h2>\n"
        + "".join(f"<p>{esc(x)}</p>\n" for x in p[20:30])
        + '<p class="noind"><img src="../images/fig.png" alt=""/></p>\n'
        + "".join(f"<p>{esc(x)}</p>\n" for x in p[30:])
        + "</body></html>\n"
    )
    ch3 = (
        '<html><head><title>3</title><style type="text/css">\n﻿p { text-align: left; text-indent: 0 }\n'
        "</style></head><body><h2>Послесловие</h2><p>Короткое послесловие без отступа.</p></body></html>"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
        ' unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="uid">urn:uuid:00000000-0000-4000-8000-000000000002</dc:identifier>'
        "<dc:title>Книга со стилем</dc:title><dc:creator>Анна Сочинская</dc:creator><dc:language>ru</dc:language>"
        "</metadata><manifest>"
        '<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="ch3" href="text/ch3.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="css" href="styles/book.css" media-type="text/css"/>'
        '<item id="fig" href="images/fig.png" media-type="image/png"/>'
        '</manifest><spine><itemref idref="ch1"/><itemref idref="ch2"/><itemref idref="ch3"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    return zipped(
        [
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/styles/book.css", b"\xef\xbb\xbf" + STYLE_CSS.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch1.xhtml", ch1.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch2.xhtml", ch2.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch3.xhtml", ch3.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/images/fig.png", PNG, zipfile.ZIP_STORED),
        ]
    )


def epub_primechaniya() -> bytes:
    """An epub3 with everything the epub reader knows: a nav table of contents, notes (an aside, endnotes in
    another document with back links, links in a <sup>, a div.footnote), pictures in the text and alone, text
    outside paragraphs, lists, quotes, verse, an epigraph, marks, line breaks and a table."""
    p = prose(11, 40)
    head = XHTML_HEAD.format(t="x")
    ch1 = (
        head + "<section epub:type='chapter'><h1>Часть первая</h1><h2 id='g1'>Глава 1</h2>"
        "<blockquote><p>Эпиграф без класса.</p><p class='text-author'>Автор</p></blockquote>"
        f"<p>{esc(p[0])} Кит <a epub:type='noteref' href='#fn1'>{{1}}</a>. Он плыл"
        "<sup><a href='notes.xhtml#n2' id='r2'>[2]</a></sup>дальше и "
        "<a href='notes.xhtml#n3' role='doc-noteref' id='r3'>3</a> молчал.</p>"
        "<aside epub:type='footnote' id='fn1'><p>Большая <em>рыба</em>, <b>очень</b> H<sub>2</sub>O.</p></aside>"
        "<aside epub:type='footnote' id='fn9'><p>Сноска, на которую никто не ссылается.</p></aside>"
        f"<p>Знак <img src='../images/a%20b.png' alt=''/> в строке. {esc(p[1])}</p>"
        "<p><img src='../images/fig.png' alt=''/></p>"
        + "".join(f"<p>{esc(x)}</p>" for x in p[2:12])
        + "<div class='paragraph'>Абзац в диве без p.</div><div>Голый текст <i>курсивом</i><p>Абзац.</p>хвост.</div>"
        "<ol start='2'><li>Второй пункт.</li><li><p>Третий пункт.</p><ul><li>Вложенный.</li></ul></li></ol>"
        "<dl><dt>Термин</dt><dd>Определение термина.</dd></dl><pre>строка кода\n  вторая</pre>"
        "<blockquote>Цитата без абзаца.<div>И див в цитате.</div></blockquote>"
        "<div epub:type='z3998:poem'><p><span>Течёт река,</span><br/>\n<span>течёт вода.</span></p>"
        "<p>Вторая строфа,<br/>её строка.</p></div>"
        "<div class='poem'><div class='stanza'><p>Один</p><p>два</p></div></div>"
        f"<p>{esc(p[12])}<br/>{esc(p[13])}</p>"
        "<p>Формула x<sup>2</sup> и <strong>жирное <em>слово</em></strong>.</p>"
        "<table><caption>Таблица.</caption><tr><th>Имя</th><th>Число</th></tr>"
        "<tr><td>Лёд<a href='#tn'><sup>*</sup></a></td><td><em>4</em> <img src='../images/fig.png'/></td></tr>"
        "<tr><td></td><td></td></tr></table><p id='tn'>Сноска к таблице.</p>"
        "<table role='presentation'><tr><td>Ячейка вёрстки.</td><td><p>Абзац в ней.</p></td></tr></table>"
        "<table><tr><td>Одна колонка.</td></tr><tr><td>Вторая строка.</td></tr></table>"
        "<div class='ws-noexport'>Служебное.</div><p>Текст<span class='mw-editsection'>[править]</span>.</p>"
        "<div class='licenseContainer'><p>Лицензия.</p></div>"
        "<div><svg xmlns:xlink='http://www.w3.org/1999/xlink'><image xlink:href='../images/fig.png'/></svg></div>"
        "</section>"
    )
    ch2 = (
        head
        + "<h2 id='g2'>Глава 2<a href='#cf' epub:type='noteref'>4</a></h2>"
        + "".join(f"<p>{esc(x)}</p>" for x in p[14:30])
        + "<p>Текст<a id='q1' href='#f5'>[5]</a>. См. <a href='#g2'>начало</a>.</p>"
        "<div class='footnote'><p><a id='f5'></a><a href='#q1'>[5]</a> Пояснение, см. <a href='#q1'>выше</a>.</p></div>"
        "<aside epub:type='footnote' id='cf'><p>Сноска в заголовке.</p></aside>"
        + "".join(f"<p>{esc(x)}</p>" for x in p[30:])
        + "<p><img src='../images/fig.png' alt=''/></p></body></html>"
    )
    notes = (
        head + "<h2>Примечания</h2><section epub:type='endnotes' role='doc-endnotes'><ol>"
        "<li id='n2' epub:type='endnote'><p>Первое примечание.</p><div class='poem'><p>Стих раз,</p><p>стих два.</p>"
        "</div><p>Второй абзац <img src='../images/fig.png'/>.</p> "
        "<a epub:type='backlink' href='ch1.xhtml#r2'>↩</a></li>"
        "<li id='n3'><p><a href='ch1.xhtml#r3'>3</a>. Третье.</p></li></ol></section></body></html>"
    )
    nav = (
        head + "<nav epub:type='toc'><ol><li><a href='text/ch1.xhtml'>Часть первая</a><ol>"
        "<li><a href='text/ch1.xhtml#g1'>Глава 1</a></li><li><span>Раздел</span><ol>"
        "<li><a href='text/ch2.xhtml#g2'>Глава 2</a></li></ol></li></ol></li>"
        "<li><a href='text/notes.xhtml'>Примечания</a></li><li><a href='about.xhtml'>Об издании</a></li></ol></nav>"
        "</body></html>"
    )
    about = (
        "<html><head><title>MediaWiki:Wsexport_about</title></head><body><h1>Об этом издании</h1>"
        "<p>Книга из Викитеки.</p><div id='ws-contributor'><ul><li>Участник</li></ul></div></body></html>"
    )
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Примечания &amp; картинки</dc:title>"
        "<dc:creator>Анна Сочинская</dc:creator><dc:language>ru</dc:language></metadata><manifest>"
        "<item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
        "<item id='ch1' href='text/ch1.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='ch2' href='text/ch2.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='notes' href='text/notes.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='about' href='about.xhtml' media-type='application/xhtml+xml'/>"
        "<item id='fig' href='images/fig.png' media-type='image/png'/>"
        "<item id='ab' href='images/a%20b.png' media-type='image/png'/>"
        "</manifest><spine><itemref idref='nav'/><itemref idref='ch1'/><itemref idref='ch2'/><itemref idref='notes' linear='no'/><itemref idref='about'/>"
        "</spine></package>"
    )
    container = "<container><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>"
    return zipped(
        [
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/nav.xhtml", nav.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch1.xhtml", ch1.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/ch2.xhtml", ch2.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/text/notes.xhtml", notes.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/about.xhtml", about.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/images/fig.png", PNG, zipfile.ZIP_STORED),
            ("OEBPS/images/a b.png", PNG, zipfile.ZIP_STORED),
        ]
    )


def epub_spisok() -> bytes:
    """An epub2 the way odd packagers write it: prefixed OPF tags in single quotes, %20 in hrefs, an item
    missing from the manifest, a document twice in the spine, chapters from the NCX, upper-case tags."""
    p = prose(12, 30)
    text = (
        "<HTML><BODY><DIV CLASS='chapter' ID='c1'><H3>Первая</H3>"
        + "".join(f"<P>{esc(x)}</P>" for x in p[:15])
        + "</DIV><div id='c2'><h3>Вторая</h3>"
        + "".join(f"<p>{esc(x)}</p>" for x in p[15:])
        + "<p>Слово<sup><a href='Notes%20File.html#n1'>1</a></sup>.</p></div></BODY></HTML>"
    )
    notes = "<html><body><p id='n1'>Короткое примечание.</p></body></html>"
    ncx = (
        "<?xml version='1.0'?><ncx><navMap><navPoint id='a'><navLabel><text>Первая\n  глава</text></navLabel>"
        "<content src='Text%20One.html#c1'/></navPoint><navPoint id='b'><navLabel><text>Вторая</text></navLabel>"
        "<content src='Text%20One.html#c2'/><navPoint id='b1'><navLabel><text>Не в книге</text></navLabel>"
        "<content src='missing.html'/></navPoint></navPoint></navMap></ncx>"
    )
    opf = (
        "<?xml version='1.0'?><opf:package xmlns:opf='http://www.idpf.org/2007/opf' version='2.0'><opf:metadata "
        "xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>Список</dc:title><dc:creator>Иван Небыль</dc:creator>"
        "</opf:metadata><opf:manifest>"
        "<opf:item id='t' href='Text%20One.html' media-type='application/xhtml+xml'/>"
        "<opf:item href='Notes%20File.html' id='n' media-type='application/xhtml+xml' />"
        "<opf:item id='toc' href='toc.ncx' media-type='application/x-dtbncx+xml'/>"
        "</opf:manifest><opf:spine toc='toc'><opf:itemref idref='t'/><opf:itemref idref='gone'/>"
        "<opf:itemref idref='t'/><opf:itemref idref='n'/></opf:spine></opf:package>"
    )
    container = '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
    return zipped(
        [
            ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
            ("META-INF/container.xml", container.encode(), zipfile.ZIP_DEFLATED),
            ("content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
            ("toc.ncx", ncx.encode(), zipfile.ZIP_DEFLATED),
            ("Text One.html", text.encode(), zipfile.ZIP_DEFLATED),
            ("Notes File.html", notes.encode(), zipfile.ZIP_DEFLATED),
        ]
    )


def fb2_stihi() -> bytes:
    """An fb2 whose look is in its elements: epigraphs, poems, citations, subtitles, empty lines."""
    p = prose(10, 30)
    body = [
        "<title><p>Стихи и проза</p></title>",
        "<epigraph><p>Слово сказано — и ладно.</p><text-author>Поговорка</text-author></epigraph>",
        '<section id="s1"><title><p>Первая</p></title>',
        "<epigraph><poem><stanza><v>Над водой туман,</v><v>за туманом — свет.</v></stanza></poem>"
        "<text-author>Сторож</text-author></epigraph>",
        "<empty-line/>",
        *[f"<p>{esc(x)}</p>" for x in p[:8]],
        "<empty-line/><empty-line/>",
        f"<p>{esc(p[8])}</p>",
        "<empty-line/><empty-line/><empty-line/><empty-line/><empty-line/>",
        f"<p>{esc(p[9])}</p>",
        "<poem><title><p>Песня</p></title><epigraph><p>Пой, пока поётся.</p></epigraph>",
        "<stanza><v>Огонь горит над морем,</v><empty-line/><v>   и волны спят внизу.</v></stanza>",
        "<stanza><v>Я жду тебя, я помню,</v><v>я лодку привезу.</v></stanza>",
        "<text-author>Сторож</text-author><date>1901</date></poem>",
        "<subtitle>* * *</subtitle>",
        "<cite><p>Свет — это обещание берега.</p><empty-line/><p>И обещание держат.</p>"
        "<text-author>Из журнала</text-author></cite>",
        *[f"<p>{esc(x)}</p>" for x in p[10:20]],
        "<empty-line/></section>",
        '<section id="s2"><title><p>Вторая</p></title><empty-line/>',
        *[f"<p>{esc(x)}</p>" for x in p[20:]],
        "<p></p><empty-line/></section>",
    ]
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><title-info><author><first-name>Олег</first-name><last-name>Небывалов</last-name></author>"
        "<book-title>Стихи и проза</book-title><lang>ru</lang></title-info></description>\n"
        "<body>\n" + "\n".join(body) + "\n</body>\n</FictionBook>\n"
    )
    return xml.encode("utf-8")


def fb2_kartinki() -> bytes:
    """An fb2 with pictures inside paragraphs (glyphs set as images), a picture alone in a paragraph and notes
    linked as comments, one of them in a chapter title."""
    p = prose(11, 30)
    body = [
        "<title><p>Знаки и гадания</p></title>",
        '<section id="k1"><title><p>Жизнеописание<a l:href="#c1" type="comment">{1}</a></p></title>',
        *[f"<p>{esc(x)}</p>" for x in p[:10]],
        '<p><strong>&lt;№2&gt;</strong> <image l:href="#g1.png"/>  <image l:href="#g2.png"/> <strong>Кунь.</strong>'
        ' Знак земли<a l:href="#c2" type="comment">{2}</a>. Второе <emphasis>предложение</emphasis>'
        ' со знаком <image l:href="#g1.png"/> внутри.</p>',
        '<p>  <image l:href="#big.png"/>  </p>',
        *[f"<p>{esc(x)}</p>" for x in p[10:20]],
        "</section>",
        '<section id="k2"><title><p>Вторая</p></title>',
        *[f"<p>{esc(x)}</p>" for x in p[20:]],
        "</section>",
    ]
    comments = (
        '<body name="comments"><section id="c1"><title><p>{1}</p></title><p>Первый комментарий.</p></section>'
        '<section id="c2"><title><p>{2}</p></title><p>Второй комментарий.</p></section></body>'
    )
    png = base64.b64encode(PNG).decode()
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><title-info><author><first-name>Олег</first-name><last-name>Небывалов</last-name></author>"
        "<book-title>Знаки и гадания</book-title><lang>ru</lang></title-info></description>\n"
        "<body>\n"
        + "\n".join(body)
        + "\n</body>\n"
        + comments
        + "\n"
        + "".join(
            f'<binary id="{n}" content-type="image/png">{png}</binary>\n' for n in ("g1.png", "g2.png", "big.png")
        )
        + "</FictionBook>\n"
    )
    return xml.encode("utf-8")


def fb2_polnota() -> bytes:
    """An fb2 with everything a book can hold: rich notes (verse, pictures, marks) in a body with its own name and
    in an unnamed linked one, an appendix body read as text, links with and without a type, sup/sub/strong, a table, images at chapter ends,
    sections without titles, titles of several paragraphs, asterisk footnotes, a full poem, indents."""
    p = prose(12, 30)
    png = base64.b64encode(PNG).decode()
    body = [
        "<title><p>Полнота</p><p>Сборник проб</p></title>",
        "<section><p>2-е издание</p><p>Текст введения, недлинный и простой.</p></section>",
        '<section id="g1"><title><p>Глава 1</p><p>Странствия<a l:href="#n1" type="note">{1}</a></p></title>',
        '<p>Кит <a l:href="#n1" type="note">{1}</a>. В организме<a l:href="#n2" type="note">[2]</a>не было'
        " воды H<sub>2</sub>O, а 4<sup>3</sup> — это <strong>шестьдесят</strong><b> четыре</b>; ещё ссылка"
        '<a l:href="#k1">[3]</a> и звезда<a l:href="#k2">*</a>.</p>',
        "<p>\xa0\xa0 1. Абзац с отступом, который уходит.</p>",
        *[f"<p>{esc(x)}</p>" for x in p[:8]],
        "<p>Это МЕХАНИЗМ* мира и ВЕЩЬ** тоже.</p>",
        "<p>* Мировоззрение (<emphasis>нем.</emphasis>).</p>",
        "<p>** Вторая сноска.</p>",
        "<p>* * *</p>",
        "<table><tr><th>Имя</th><th>Число</th></tr><tr><td><emphasis>Лёд</emphasis></td>"
        '<td>4<a l:href="#n1" type="note">1</a></td></tr><tr><td>Пар</td><td/></tr></table>',
        "<section><p>Без заголовка, но внутри главы.</p></section>",
        '<image l:href="#end1.png"/>',
        "</section>",
        '<section id="g2"><title><p>Часть вторая:</p><empty-line/><p>Стихи</p></title>',
        '<image l:href="#start2.png"/>',
        "<poem><title><p>Песня</p></title><epigraph><p>Пой.</p><text-author>Он</text-author></epigraph>"
        "<subtitle>Первая часть</subtitle><stanza><title><p>I</p></title><subtitle>тихо</subtitle>"
        "<v>\xa0\xa0Огонь горит над морем,</v><v>и волны спят внизу.</v></stanza><date>1901</date></poem>",
        *[f"<p>{esc(x)}</p>" for x in p[8:30]],
        '<image l:href="#end2.png"/>',
        "</section>",
    ]
    notes = (
        '<body name="Примечания"><title><p>Примечания</p></title>'
        '<section id="n1"><title><p>1</p></title><p>Вот <emphasis>эти</emphasis> стихи: <image l:href="#s.png"/>.</p>'
        "<poem><stanza><v>Раз строка,</v><v>два строка.</v></stanza></poem>"
        '<image l:href="#t.png"/><p>Конец <strong>сноски</strong> x<sup>2</sup>, ссылка'
        ' <a l:href="#n2" type="note">[2]</a>.</p></section>'
        '<section id="n2"><p>Простая   сноска.</p></section>'
        '<section id="k1"><p>Без типа, но в примечаниях.</p></section></body>'
        '<body><section id="k2"><p>Тело без имени, на него ссылаются.</p></section></body>'
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><title-info><author><first-name>Олег</first-name><last-name>Небывалов</last-name></author>"
        "<book-title>Полнота</book-title><lang>ru</lang></title-info></description>\n"
        "<body>\n"
        + "\n".join(body)
        + "\n</body>\n"
        + '<body name="Приложение"><title><p>Приложение</p></title><section id="app1"><title><p>Даты</p></title>'
        + "".join(f"<p>{esc(x)}</p>" for x in p[:3])
        + "</section></body>\n"
        + notes
        + "\n"
        + "".join(
            f'<binary id="{n}" content-type="image/png">{png}</binary>\n'
            for n in ("end1.png", "start2.png", "end2.png", "s.png", "t.png")
        )
        + "</FictionBook>\n"
    )
    return xml.encode("utf-8")


def fb2_nebrezhno() -> bytes:
    """A carelessly made fb2: windows-1251, no FB2 namespace, an undeclared `l:` prefix, HTML entities, a stray
    `&` and a picture whose base64 is broken. None of it may cost the book."""
    p = prose(13, 24)
    png = base64.b64encode(PNG).decode()
    paras = "".join(f"<p>{esc(x)}</p>\n" for x in p)
    xml = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        "<FictionBook>\n"
        "<description><title-info><author><first-name>Вера</first-name><last-name>Придумова</last-name></author>"
        "<book-title>Небрежная книга</book-title><lang>ru</lang></title-info></description>\n"
        "<body><section><title><p>Глава&nbsp;первая</p></title>\n"
        "<p>&laquo;Он&raquo;&nbsp;пришёл &mdash; Smith & Co &amp; &unknown; &#1046;&hellip;"
        ' Сноска<a l:href="#n1" type="note">1</a>.</p>\n'
        '<image l:href="#bad.png"/><image l:href="#good.png"/>\n' + paras + "</section></body>\n"
        '<body name="notes"><section id="n1"><p>Примечание&nbsp;одно.</p></section></body>\n'
        '<binary id="bad.png" content-type="image/png">!!!не base64</binary>\n'
        f'<binary id="good.png" content-type="image/png">{png}</binary>\n'
        "</FictionBook>\n"
    )
    return xml.encode("cp1251")


def fb2_kraya() -> bytes:
    """An fb2 at the edges of the rules (2026-10 review): quotes and spaces at removed note links, a link to a
    missing note, a paragraph of only a note link, text after a nested chapter, `5*3` and a link inside an
    asterisk note, ids the file repeats, deep nesting, HTML5 entities, a binary id with a folder, a nested stanza,
    text before the first chapter and a first description without a title-info."""
    p = prose(14, 30)
    png = base64.b64encode(PNG).decode()
    body = [
        '<p><a l:href="#n2" type="note">2</a></p><epigraph><p>Слово до первой главы.</p></epigraph>',
        '<section id="g1"><title><p>Края</p></title>',
        '<p id="b5">Он сказал <a l:href="#n1" type="note">1</a>"Привет" и ушёл. Слово <a l:href="#n1" type="note">2</a>".'
        ' Слово <a l:href="#n2" type="note">{3}</a> следующее, и <a l:href="#nope" type="note">[4]</a> без сноски.</p>',
        '<p id="dup">Итого 5*3 = 15, а МЕХАНИЗМ* мира &alpha; &hearts; &frac13; &notit; &ampx; &NotEqualTilde;.</p>',
        '<p>* Пояснение<a l:href="#n1" type="note">[1]</a> и <emphasis>курсив</emphasis>.</p>',
        '<p> <a l:href="#n2" type="note">5</a> </p>',
        *[f"<p>{esc(x)}</p>" for x in p[:10]],
        '<section id="g2"><title><p>Вложенная</p></title><image l:href="#dir/pic.png"/>',
        *[f"<p>{esc(x)}</p>" for x in p[10:14]],
        "</section>",
        '<p id="dup">Снова текст родителя.</p>',
        "<poem><stanza><v>Раз строка,</v><stanza><v>два строка,</v></stanza><v>три строка.</v></stanza></poem>",
        "<p>" + "<emphasis>" * 300 + "Глубоко." + "</emphasis>" * 300 + " хвост</p>",
        "<cite>" * 300 + "<p>Цитата.</p><p>Вторая.</p>" + "</cite>" * 300,
        *[f'<p id="b{40 + i}">{esc(x)}</p>' for i, x in enumerate(p[14:])],
        "</section>",
    ]
    notes = (
        '<body name="notes"><section id="n1"><p>Первая сноска.</p></section>'
        '<section id="n2"><p>Вторая сноска.</p></section></body>'
    )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">\n'
        "<description><document-info><id>kraya</id></document-info></description>\n"
        "<description><title-info><author><first-name>Олег</first-name><last-name>Небывалов</last-name></author>"
        "<book-title>Края</book-title><lang>ru</lang></title-info></description>\n"
        "<body>\n" + "\n".join(body) + "\n</body>\n" + notes + "\n"
        f'<binary id="dir/pic.png" content-type="image/png">{png}</binary>\n'
        "</FictionBook>\n"
    )
    return xml.encode("utf-8")


def fb2_bityj() -> bytes:
    """A broken fb2: a BOM and a blank line before the declaration, an encoding name only Apple knew
    (x-cp1251), a byte windows-1251 does not define, references to characters XML has not, and a body of bare
    paragraphs without sections or a title."""
    p = prose(15, 30)
    xml = (
        '<?xml version="1.0" encoding="x-cp1251"?>\n'
        "<FictionBook>\n"
        "<description><title-info><author><first-name>Вера</first-name><last-name>Придумова</last-name></author>"
        "<book-title>Битая книга</book-title><lang>ru</lang></title-info></description>\n"
        "<body><p>Знак&#1; и&#xFFFE; &#99999999999;ещё ЗАМЕНА.</p>\n"
        + "".join(f"<p>{esc(x)}</p>\n" for x in p)
        + "</body>\n</FictionBook>\n"
    )
    return b"\xef\xbb\xbf\r\n" + xml.encode("cp1251").replace("ЗАМЕНА".encode("cp1251"), b"\x98")


def wrap(text: str, width: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split(" "):
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return [*lines, cur] if cur else lines


def txt_zapiski() -> bytes:
    """Hard-wrapped, blank lines between most paragraphs: a heading stands between blank lines, while capitals and
    a number on a line of their own inside a paragraph are text; a word hyphenated at a line end, an asterisk
    footnote, a shout in capitals."""
    p = prose(6, 32)
    out = ["Часть первая", "", "Глава 1", ""]
    for i, para in enumerate(p):
        if i == 4:
            out += [
                "Маяк стоял на мысу, и каждую ночь его огонь проходил по воде пере-",
                "носом через залив, до самого дальнего берега, где спали рыбаки, и",
                "на двери сторожки было написано крупно, так что видно было с воды:",
                "ВХОД ВОСПРЕЩЁН",
                "и ещё число, которого никто не мог объяснить ни летом, ни зимой, в",
                "12",
                "часов ночи. Это был МЕХАНИЗМ* старый и верный, как сам сторож.",
                "",
                "* Устройство (нем.).",
                "",
                "— СТОЙ!",
                "",
            ]
        if i == 10:
            out += ["II", ""]
        if i == 20:
            out += ["ГЛАВА ТРЕТЬЯ", ""]
        lines = wrap(para, 68)
        out += ["    " + lines[0], *lines[1:]] if i % 2 else lines
        if i % 3 != 1:
            out.append("")
    return "\r\n".join(out).encode("utf-8")


def txt_rukopis() -> bytes:
    """Hard-wrapped with no blank lines at all, UTF-16 with a byte order mark: an indented line starts a
    paragraph, a centred heading is a line of its own, `***` is a scene break, dialogue in capitals is text."""
    p = prose(9, 30)
    centre = " " * 28
    out = [centre + "ГЛАВА 1"]
    for i, para in enumerate(p):
        if i == 6:
            out += ["     — СТОЙ! — крикнул сторож с берега.", "     Это была ВЕЩЬ** простая, но нужная."]
            out += ["     ** Так в рукописи."]
        if i == 12:
            out.append(centre + "***")
        if i == 20:
            out.append(centre + "Глава 2")
        lines = wrap(para, 66)
        out += ["     " + lines[0], *lines[1:]]
    return b"\xff\xfe" + ("\r\n".join(out) + "\r\n").encode("utf-16-le")


def txt_zametki() -> bytes:
    """One line per paragraph with blank lines between, in mac-cyrillic: a capital line, a lone number, ordinal
    and keyword headings; dialogue and list items in capitals or with numbers are text, and so is a capital line
    with text right under it; scene breaks; footnotes with one and two asterisks, a lone asterisk that is none."""
    p = prose(10, 30)
    out = ["Заметки на полях", "ЗИМА"]
    for i, para in enumerate(p):
        if i == 3:
            out += ["— СТОЙ!", "1. Первое правило: молчать.", "2. Второе правило: ждать."]
        if i == 6:
            out += ["КОНЕЦ ЗИМЫ!\n" + para]
            continue
        if i == 9:
            out += ["Это была ВЕЩЬ* простая и ДЕЛО** важное.", "* Первая сноска.", "** Вторая сноска."]
        if i == 12:
            out += ["* * *"]
        if i == 15:
            out += ["3"]
        if i == 20:
            out += ["Глава четвёртая", "---"]
        if i == 25:
            out += ["Эпилог", "*Важно* сказать: звезда* здесь не сноска."]
        out.append(para)
    return ("\n\n".join(out) + "\n").encode("mac_cyrillic")


def txt_dnevnik() -> str:
    p = prose(7, 30)
    out = ["Глава 1"]
    for i, para in enumerate(p):
        if i == 15:
            out.append("Глава 2")
        out.append(para)
    # koi8-r has no typographic quotes, dashes or ellipsis: the diary is typed plainly
    text = "\n".join(out) + "\n"
    return text.replace("«", '"').replace("»", '"').replace("—", "-").replace("…", "...")


def pdf_bytes(pages: list[list], title: str, author: str) -> bytes:
    """A PDF with one Helvetica font whose codes are cp1251 and a ToUnicode map, so its text layer reads
    back as Russian in pypdf and PDFKit alike, and whose widths are given (500, a space 250), so both measure
    a line alike. No layout engine: a page is its running head, its lines and its number; a line is a string
    (11 pt, one under the other) or (text, size, x, y) set where it says."""

    def pdf_str(s: str) -> bytes:
        b = s.encode("cp1251")
        return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"

    def utf16(s: str) -> bytes:
        return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode() + b">"

    codes = []
    for c in range(0x20, 0x100):
        try:
            codes.append((c, bytes([c]).decode("cp1251")))
        except UnicodeDecodeError:
            continue
    chunks = [codes[i : i + 100] for i in range(0, len(codes), 100)]
    cmap = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
    )
    for chunk in chunks:
        cmap += f"{len(chunk)} beginbfchar\n".encode()
        for c, u in chunk:
            cmap += f"<{c:02X}> <{ord(u):04X}>\n".encode()
        cmap += b"endbfchar\n"
    cmap += b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"

    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    def stream(data: bytes) -> bytes:
        return b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"

    catalog = add(b"")  # filled below
    pages_obj = add(b"")
    tounicode = add(stream(cmap))
    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 255"
        + b" /Widths ["
        + b" ".join(b"250" if c == 32 else b"500" for c in range(32, 256))
        + b"] /ToUnicode "
        + str(tounicode).encode()
        + b" 0 R >>"
    )
    info = add(b"<< /Title " + utf16(title) + b" /Author " + utf16(author) + b" >>")
    kids = []
    for page in pages:
        body = [b"BT /F1 9 Tf 56 800 Td " + pdf_str(page[0]) + b" Tj ET"]
        body.append(b"BT /F1 11 Tf 14 TL 56 770 Td")
        for ln in page[1:-1]:
            if isinstance(ln, str):
                body.append(pdf_str(ln) + b" Tj T*")
        body.append(b"ET")
        for ln in page[1:-1]:
            if not isinstance(ln, str):
                text, size, x, y = ln
                body.append(f"BT /F1 {size} Tf {x} {y} Td ".encode() + pdf_str(text) + b" Tj ET")
        body.append(b"BT /F1 9 Tf 290 30 Td " + pdf_str(page[-1]) + b" Tj ET")
        content = add(stream(b"\n".join(body)))
        kids.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_obj).encode()
                + b" 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
                + str(font).encode()
                + b" 0 R >> >> /Contents "
                + str(content).encode()
                + b" 0 R >>"
            )
        )
    objs[catalog - 1] = b"<< /Type /Catalog /Pages " + str(pages_obj).encode() + b" 0 R >>"
    objs[pages_obj - 1] = (
        b"<< /Type /Pages /Kids [" + b" ".join(f"{k} 0 R".encode() for k in kids) + b"] /Count %d >>" % len(kids)
    )
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def pdf_doklad() -> bytes:
    p = prose(8, 30)
    lines: list[str] = ["Глава 1"]
    for i, para in enumerate(p):
        if i == 15:
            lines.append("Глава 2")
        if i == 3:
            # a word broken across lines, as typesetters do
            lines += [
                "Маяк стоял на мысу, и каждую ночь его огонь проходил по воде пере-",
                "носом через залив, до самого дальнего берега, где спали рыбаки.",
                "Так было всегда.",
            ]
        lines += wrap(para, 66)
    per = 30
    pages = []
    for n, i in enumerate(range(0, len(lines), per), 1):
        pages.append(["П. ВЫДУМКИН. ДОКЛАД О МАЯКАХ", *lines[i : i + per], str(n + 4)])
    return pdf_bytes(pages, "Доклад о маяках", "Пётр Выдумкин")


def pdf_lekcii() -> bytes:
    """A typeset PDF: a table of contents with dot leaders, headings in a larger font with space around them,
    paragraphs shown by a first-line indent, footnotes in a smaller font at a page's foot with `*` markers in
    the text, a word broken for the line and a hyphenated one (что-то) broken at its hyphen."""
    p = prose(9, 40)
    p[1] = p[1] + " На мысу стоял старый маяк*, и его огонь был виден далеко."
    p[2] = "Где-то за холмом звенел колокол, и что-то шумело у самой воды всю ночь."
    p[5] = p[5] + " Сторож** записывал в журнал всё, что видел."
    p[8] = "Маяк стоял на мысу, и каждую ночь его огонь проходил по воде пере-носом через залив."
    notes = {
        "*": ["* Маяк построили в 1890 г., и с тех пор он светит каждую ночь, кроме", "одной."],
        "**": ["** Сторожем был Пётр Выдумкин."],
    }
    flow: list[tuple[str, object]] = [("h", "Лекция первая")]
    for i, para in enumerate(p):
        if i == 7:
            flow.append(("h", "Лекция вторая"))
        lines = wrap(para, 60)
        if len(lines) == 1:  # set ragged, a line of a full paragraph could pass for one of full width
            lines = wrap(para, len(para) // 2 + 4)
        if i == 20:  # a paragraph of one line
            lines = ["— Свет виден издалека, — сказал сторож."]
        if i == 2:  # что-то broken at its own hyphen
            lines = ["Где-то за холмом звенел колокол, и что-", "то шумело у самой воды всю ночь."]
        if i == 8:  # a word broken for the line
            lines = ["Маяк стоял на мысу, и каждую ночь его огонь проходил по воде пере-", "носом через залив."]
        flow.append(("p", lines))
    pages: list[list] = [
        [
            "П. ВЫДУМКИН. ЛЕКЦИИ О МАЯКАХ",
            ("Содержание", 15, 250, 740),
            ("Лекция первая . . . . . . . . . . . . 2", 11, 56, 700),
            ("Лекция вторая . . . . . . . . . . . . 3", 11, 56, 686),
            ("Примечания . . . . . . . . . . . . . 4", 11, 56, 672),
            "1",
        ]
    ]
    page: list = []
    feet: list[str] = []
    y = 770.0

    def new_page() -> None:
        nonlocal page, feet, y
        foot = [(t, 9, 56, 80 - 11 * k) for k, t in enumerate(feet)]
        pages.append(["П. ВЫДУМКИН. ЛЕКЦИИ О МАЯКАХ", *page, *foot, str(len(pages) + 1)])
        page, feet, y = [], [], 770.0

    for kind, item in flow:
        if kind == "h":
            if page:
                y -= 30
            page.append((item, 15, 56, y))
            y -= 36
            continue
        for k, ln in enumerate(item):
            if y < 140:
                new_page()
            page.append((ln, 11, 70 if k == 0 else 56, y))
            for m, body in notes.items():
                if re.search(r"(?<![*])" + re.escape(m) + r"(?![*])", ln):
                    feet += body
            y -= 14
    new_page()
    return pdf_bytes(pages, "Лекции о маяках", "Пётр Выдумкин")


def sources() -> list[tuple[str, bytes, dict]]:
    """(file name, bytes, case options). `same_as`: the phone reads this as that other file."""
    pdf = pdf_doklad()
    dnevnik = txt_dnevnik()
    stub = (
        '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        "<description><title-info><book-title>Закрыто</book-title></title-info></description><body><section>"
        "<p>Книга заблокирована по требованию правообладателя.</p></section></body></FictionBook>"
    )
    return [
        ("povest.fb2", fb2_povest(), {}),
        ("rasskaz-1251.fb2", fb2_1251(), {}),
        ("sbornik.fb2.zip", fb2_sbornik(), {}),
        ("kniga.epub", epub3(), {}),
        ("zapiska.epub", epub2(), {}),
        ("stil.epub", epub_stil(), {}),
        ("primechaniya.epub", epub_primechaniya(), {}),
        ("spisok.epub", epub_spisok(), {}),
        ("stihi.fb2", fb2_stihi(), {}),
        ("kartinki.fb2", fb2_kartinki(), {}),
        ("polnota.fb2", fb2_polnota(), {}),
        ("nebrezhno.fb2", fb2_nebrezhno(), {}),
        ("kraya.fb2", fb2_kraya(), {}),
        ("bityj-1251.fb2", fb2_bityj(), {}),
        ("zapiski.txt", txt_zapiski(), {}),
        ("dnevnik-1251.txt", dnevnik.encode("cp1251"), {}),
        # the same diary in the other Russian code pages: told apart by how Russian each reads
        ("dnevnik-koi8.txt", dnevnik.encode("koi8_r"), {"same_as": "dnevnik-1251.txt"}),
        ("dnevnik-866.txt", dnevnik.encode("cp866"), {"same_as": "dnevnik-1251.txt"}),
        ("rukopis.txt", txt_rukopis(), {}),
        ("zametki.txt", txt_zametki(), {}),
        ("doklad.pdf", pdf, {}),
        ("doklad.pdf.zip", zipped([("doklad.fbd", b"<fbd/>", 8), ("doklad.pdf", pdf, 8)]), {}),
        ("lekcii.pdf", pdf_lekcii(), {}),
        ("zaglushka.fb2", stub.encode("utf-8"), {"error": "заглушка"}),
        ("korotko.txt", "Всего несколько слов, и всё.\n".encode(), {"error": "500 слов"}),
        ("prochee.zip", zipped([("a.docx", b"x", 8), ("b.docx", b"y", 8)]), {"error": "нет книги"}),
    ]


def stem(name: str) -> str:
    low = name.lower()
    for ext in (".fb2.zip", ".pdf.zip", ".fb2", ".epub", ".pdf", ".txt", ".zip"):
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def expect(name: str, data: bytes) -> tuple[dict, bytes | None]:
    """What the pipeline makes of one file: add_book's unwrap, sniff, extractor, check and merge."""
    case: dict = {"file": name}
    try:
        data, hint = add_book.unwrap(data, name)
        kind = add_book.sniff(data, hint)
        case["kind"] = kind
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / (stem(name) + EXT[kind])
            src.write_bytes(data)
            book = EXTRACT[kind](src)
            add_book.check_real_book(book, 1)
            book = merge([("", book)])
            images = Path(tmp) / "images"
            case["images"] = (
                {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(images.iterdir())}
                if images.is_dir()
                else {}
            )
    except SystemExit as e:
        case["python_error"] = str(e)
        return case, None
    case["title"] = book["title"]
    case["author"] = book["author"]
    case["text_end"] = text_end(book)
    case["fragment_note"] = add_book.fragment_note(book)
    case["words"] = sum(len(b["text"].split()) for b in book["blocks"])
    return case, dump_book(book).encode("utf-8")


# the splitter's hard cases (initials, abbreviations, list numbers), as the 2026-10 audit found them
SENTENCES = [
    "Он пришёл. Она ушла! Кто это? Никто.",
    "– Вот именно… – сказала Мара. «Ну да?» – спросил я.",
    "Это было в 1990 г. в Москве. Потом т. е. позже.",
    "Это был лишь повод Н. Жирардо написать книгу. Он её написал.",
    "© Пелевин В. О., текст, 2017",
    "Перевод Ю.Щ. Шуцкого. Издание второе.",
    "Редактор В. О. Пелевин. Корректор А. Б. Ветров.",
    "Translated by J. R. Smith. Second edition.",
    "Об этом сказано выше (гл. 7). Далее.",
    "Подробнее (см. с. 135) у автора. Дальше.",
    "Жил в III в. до н. э., его не знали.",
    "Слово «дао» (кит. 道) значит путь. Второе.",
    "Это т. н. великий предел, ср. Беспредельное. Конец.",
    "Он изд. в 1900, ок. 1900, акад. Иванов, англ. mind, нем. «Geist», лат. (homo). Всё.",
    "See e.g. the book, i.e. the man Mr. Smith and Dr. Watson, vol. 2, p. 15, pp. 3-4, ch. 5. Next.",
    "Он любил травы, камни и т. д. Если спросить, молчал.",
    "Реки, горы и т. п. Всё это.",
    "Лао-цзы, Чжуан-цзы и др. Они учили.",
    "Книги, свитки и т.д. Потом.",
    "Травы, камни и т. д. всё росло.",
    "Это т. е. Путь. Конец.",
    "Это т. е. путь. Конец.",
    "Под нами проплыл кит. Он плыл на север.",
    "Кит. Он плыл.",
    "Жил в III в. До него не знали.",
    "Я дал им. Они ушли.",
    "Слово (кит. 天) значит небо.",
    "См. с. 135 и гл. 7, ст. 3. Конец.",
    "Это рис. (вверху) и пер. с англ. «Mind». Всё.",
    "Около 5 ч. утра.",
    "Автор В. С. Петров. Дальше.",
    "Улица ул. Ленина, проф. Иванов, св. Пётр. Конец.",
    "См. Пятую главу, ср. Шестую. Конец.",
    "Он и др. учёные пришли. Конец.",
    "So it goes etc. Then more.",
    "1. Даос в буддизме. Второе.",
    "12. Пункт двенадцатый.",
    "IV. Четвёртый раздел. Текст.",
    "а) первое, б) второе.",
    "   1. Отступ стихом.",
    "Это было в 1999. Потом всё.",
    "Первая строка.\nВторая строка.",
    "Заголовок без точки",
    "Он думал… и молчал. Потом ушёл.",
    # X32: a single capital that answers, a symbol in another script, weak abbreviations, a quote opening
    "— Кто там? — Я. Ну открывай же.",
    "Кто там? Я. Ну и что.",
    "«Кто там?» — Я. Ну.",
    "Сказал он: — Я. Нет. — А. Б. Петров пришёл.",
    "— Я. А ты? Это сделал Я. Никто.",
    "Витамин C. Его много. Translated by J. Smith. Next.",
    "Перевод Н.\nЖирардо. Автор Н. Жирардо.",
    "5 млн. Это много, 3 млрд. рублей, тыс. Дальше, стр. 5, и др. Они, и пр. учёные.",
    "Слово от англ. Love, нем. Geist, лат. Homo. Это т. е. Путь.",
    "Он ушёл. 'Привет', сказал я.",
    "1. A. Первый пункт. A. Второй.",
]


def model_book() -> dict:
    """A book with every optional key of the book.json model (pipeline/extract_text.py dump_book)."""
    p = {"images": [], "id": "b0", "kind": "p", "chapter": 0, "stanza": None}
    text = "Вода 4³ и H₂O, сказал Кит. Твёрдо."
    table = "Имя\tЧисло\nЛёд\t4"
    return {
        "title": "Модель",
        "author": "Никто",
        "chapters": [{"id": "s0", "title": "Глава", "level": 1, "first_block": 0}],
        "blocks": [
            {
                **p,
                "images": [{"src": "images/a.png", "w": 3, "h": 4}],
                "text": text,
                "em": [[0, 4]],
                "strong": [[27, 33]],
                "sup": [[6, 7]],
                "sub": [[11, 12]],
                "notes": [{"pos": 25, "id": "n1", "m": "721"}, {"pos": 34, "id": "n2"}],
                "sentences": split_sentences(text),
                "audio": True,
                "pics": [{"pos": 5, "src": "images/b.png"}],
                "st": {"a": "c"},
            },
            {
                **p,
                "id": "b1",
                "kind": "table",
                "images": [{"src": "images/c.png", "after": True}],
                "text": table,
                "em": [],
                "notes": [],
                "sentences": [[0, 9], [10, 15]],
                "audio": False,
                "rows": [[[0, 3, 1], [4, 9, 1]], [[10, 13], [14, 15]]],
            },
        ],
        "notes": {
            "n1": "Простое примечание.",
            "n2": {
                "text": "Стих один\nстрока два\n\nВторой абзац 2⁵.",
                "em": [[0, 4]],
                "strong": [[5, 9]],
                "sup": [[36, 37]],
                "sub": [[10, 16]],
                "pics": [{"pos": 22, "src": "images/d.png"}],
                "kinds": [[0, 20, "verse"]],
            },
            "n3": {"text": "Один абзац.", "em": [[0, 4]]},
        },
    }


def txt_cases() -> list[tuple[str, bytes]]:
    """Small plain texts, a rule each (tests/test_txt.py says what each comes to). `txt.json` holds them with the
    book.json the pipeline makes of each, for the phone."""
    plain = "Глава 1\n\nМама мыла раму, а папа читал газету. Ёжик съел яблоко.\n"
    caps = "ГЛАВА 1\n\nВСЁ НАПИСАНО ЗАГЛАВНЫМИ БУКВАМИ, КАК ТЕЛЕГРАММА.\n\nКОНЕЦ\n\nИ ЭТО ТОЖЕ ПРОСТО ТЕКСТ.\n"
    headings = (
        "Часть вторая\n\nГлава I\n\nТекст первой главы, обычный и спокойный.\n\nCHAPTER TWELVE\n\nText of it.\n\n"
        "IV.\n\nЕщё немного текста.\n\n12\n\nПотом ещё текст.\n\nКОНЕЦ ПУТИ\n\nКонец пути был близок.\n\n"
        "Пролог\n\nНачало всего.\n\nКнига третья\nГлава 7. Встреча\n\nОни встретились.\n"
    )
    not_headings = (
        "Текст.\n\nЗИМА!\nПришла зима.\n\n— СТОЙ!\n\n— Глава 1, — сказал он.\n\n1. Первое.\n\nГлава семьи\n\n"
        "ЧАСТЬ, КОТОРУЮ НЕ ЖДАЛИ,\n\nВ\n"
    )
    breaks = "Раз.\n\n* * *\n\nДва.\n\n***\n\nТри.\n\n---\n\n— — —\n\nЧетыре.\n\n* * *\n\nГлава 2\n\nПять.\n\n* * *\n"
    hard = (
        "         Глава 1\n"
        "    Маяк стоял на мысу, и каждую ночь его огонь проходил по воде пере-\n"
        "носом через залив, до самого дальнего берега, где спали рыбаки, и\n"
        "так было всегда.\n"
        "Сторож зажигал огонь каждый вечер, и все корабли видели его свет\n"
        "издалека, даже в самый густой туман над холодной водой залива.\n"
        "Старики помнили это, и дети тоже помнили, и рыбаки на берегу.\n"
        "    Так было всегда, сколько помнили старики в той деревне у моря.\n"
    )
    notes = (
        "Это МЕХАНИЗМ* мира, и ВЕЩЬ** тоже.\n\n* Устройство (нем.).\n\n** Вторая сноска.\n\nТам «слово»*.\n\n"
        "* Третья сноска.\n\n*Важно* сказать.\n\nЕщё абзац.\n"
    )
    first_line = "Текст до главы.\n\nГлава 3\nТекст главы сразу под заголовком.\n\nЗИМА\nПришла зима.\n\nКонец.\n"
    last_line = (
        "Маяк стоял на мысу, и каждую ночь его огонь проходил по воде\n"
        "через залив до самого дальнего берега, где спали рыбаки.\n"
        "Глава 3\n\n"
        "Сторож зажигал огонь каждый вечер, и все корабли видели его\n"
        "свет издалека, даже в самый густой туман над водой залива.\n"
        "ЗИМА\n\n"
        "Старики помнили это, и дети тоже помнили, и рыбаки на берегу,\n"
        "и все в деревне у моря помнили этот огонь и этот старый маяк.\n"
    )
    notes_near = (  # a body within three paragraphs of its marker; a `*word*` pair is emphasis, not a marker
        "Это *важное* слово и хлеб.\n\nВторой абзац.\n\nТретий.\n\n*хлеб с маслом\n\nСлово* тут.\n\nДва.\n\n"
        "Три.\n\nЧетыре.\n\n* Далёкая сноска.\n\nСлово* здесь.\n\n* Близкая сноска.\n"
    )
    prologue = "Пролог к этой истории написал мой дед.\n\nТекст.\n\nЭпилог\n\nКонец.\n\nПредисловие автора\n\nЕщё.\n"
    hyphens = (  # a hyphen at a line end stays in что-то and in a word the text spells with it elsewhere
        "    Северо-западный ветер дул над заливом всю ночь, и рыбаки сидели\n"
        "у огня, слушая шум волн, а на другой день снова дул северо-\n"
        "западный ветер, и что-то шумело за стеной, и снова что-\n"
        "то шумело у воды, и никто не знал, откуда шла та пере-\n"
        "мена погоды над морем, над берегом и над старым маяком.\n"
        "    Второй абзац идёт следом за первым и тоже довольно длинный,\n"
        "чтобы ширина колонки была видна, как в настоящей книге.\n"
    )
    heading_marks = "Глава 1\u0301\n\nТекст.\n\nГлава первая\u0301\n\nЕщё.\n\nГлава 1½\n\nИ ещё.\n\nЧасть_2\n\nКонец.\n"
    # a lone byte and a cut sequence in a UTF-8 text: replaced, the rest still UTF-8
    stray = ("Глава 1\n\n" + "Мама мыла раму, а папа читал газету. " * 20 + "\n").encode()
    stray = b",\xff ".join(stray.split(b", ", 1)).replace(b"\xd0\xb0 ", b"\xd0\xb0\xe2\x82 ", 1)
    return [
        ("utf8", plain.encode("utf-8")),
        ("utf8-bom", b"\xef\xbb\xbf" + plain.encode("utf-8")),
        ("utf16le-bom", b"\xff\xfe" + plain.encode("utf-16-le")),
        ("utf16be-bom", b"\xfe\xff" + plain.encode("utf-16-be")),
        ("utf32le-bom", b"\xff\xfe\x00\x00" + plain.encode("utf-32-le")),
        ("utf32be-bom", b"\x00\x00\xfe\xff" + plain.encode("utf-32-be")),
        ("cp1251", plain.encode("cp1251")),
        ("koi8-r", plain.encode("koi8_r")),
        ("cp866", plain.encode("cp866")),
        ("mac-cyrillic", plain.encode("mac_cyrillic")),
        ("caps-koi8-r", caps.encode("koi8_r")),
        ("latin-1", "Café au lait, déjà vu.\n\nII\n\nÜber München.\n".encode("latin-1")),
        ("headings", headings.encode()),
        ("not-headings", not_headings.encode()),
        ("breaks", breaks.encode()),
        ("hard-wrapped", hard.encode()),
        ("notes", notes.encode()),
        ("first-line-heading", first_line.encode()),
        ("last-line-heading", last_line.encode()),
        ("crlf", "Глава 1\r\n\r\nТекст.\r\n\r\nЕщё.\r\rИ ещё.\r\n".encode()),
        ("empty", b""),
        ("notes-near", notes_near.encode()),
        ("prologue-sentence", prologue.encode()),
        ("hyphen-words", hyphens.encode()),
        ("heading-marks", heading_marks.encode()),
        ("utf8-stray-byte", stray),
        ("cp1251-few-letters", ("Chapter one. " * 40 + "Привет").encode("cp1251")),
        ("utf16le-no-bom", plain.encode("utf-16-le")),
        ("utf16be-no-bom", plain.encode("utf-16-be")),
    ]


def extract_txt_bytes(name: str, data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / (name + ".txt")
        src.write_bytes(data)
        return extract_txt.extract(src)


def pdf_line_cases() -> list[tuple[str, list[list[tuple]]]]:
    """Pages of lines as a PDF's text layer gives them (text, x0, x1, y, size, bold), a rule each (tests/test_pdf.py
    says what each comes to). `pdf.json` holds them with the book.json extract_pdf.book_from_lines makes of them, so
    the phone's rules after PDFKit are held to the pipeline's, whatever PDFKit reads."""

    def page(lines: list, foot: list[str] = (), top: list[tuple] = (), bottom: list[tuple] = ()) -> list[tuple]:
        out = [(t, 280.0, 300.0, 790.0, sz, sz == 11.0) for t, sz in top]  # a numeral: centred, bold or larger
        y = 760.0
        for t in lines:
            t, x0 = (t[1:], 70.0) if t.startswith(">") else (t, 56.0)  # `>`: the first line of a paragraph
            out.append((t, x0, 540.0 if len(t) > 55 else x0 + 6.0 * len(t), y, 11.0, False))
            y -= 14.0
        y -= 30.0
        for t in foot:
            out.append((t, 56.0, 56.0 + 4.0 * len(t), y, 8.0, False))
            y -= 10.0
        out += [(t, 290.0, 300.0, 40.0, sz, False) for t, sz in bottom]
        return out

    def text(seed: int, n: int) -> list[str]:
        lines: list[str] = []
        for para in prose(seed, n):
            w = wrap(para, 64)
            lines += [">" + w[0], *w[1:]]
        return lines

    folios = [
        page([*text(1, 2), "Он ждал этого так долго, как ждут весны… 1945"], bottom=[("7", 9.0)]),
        page(text(2, 2), top=[("II", 11.0)], bottom=[("8", 9.0)]),
        page(text(3, 2), bottom=[("civil", 11.0), ("9", 9.0)]),
        page(text(4, 2), top=[("III", 11.0)], bottom=[("10", 9.0)]),
        page(text(5, 2), top=[("IV", 16.0)], bottom=[("11", 9.0)]),
        page(["Лекция первая ........ 12", "Лекция вторая ........ 30", "Лекция третья ........ 41", *text(6, 1)]),
    ]
    notes = [
        page(
            [
                *text(7, 1),
                ">Вода и CO2 в воздухе, см. т.1 и гл.2, а слово1 стоит тут, и",
                "знак \ue000 тоже, и всё это тянется до самого правого края",
                "строки, и дальше.",
                *text(8, 1),
            ],
            foot=["1 Первая сноска к слову.", "2 Вторая сноска, метки которой", "нет в тексте, и она идёт"],
        ),
        page([*text(9, 2)], foot=["12 апреля всё кончилось."]),
        page([*text(10, 2)], foot=["* * *"]),
        page(
            [">Звезда* стоит здесь, а текст тянется до самого правого края", "строки.", *text(11, 1)],
            foot=["* Сноска со звездой."],
        ),
    ]
    return [("folios", folios), ("notes", notes)]


def epub_cases() -> list[tuple[str, bytes]]:
    """Small epubs, a rule each (tests/test_epub.py says what each comes to): `epub.json` holds them with what
    extract_epub makes of them, for the phone. Not books by size, so the importer's word count is not applied."""
    head = (
        '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"'
        ' xmlns:epub="http://www.idpf.org/2007/ops"><head><title>x</title></head><body>'
    )
    container = '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'

    def epub(
        docs: dict, more: list | None = None, nav: str | None = None, cname: str = "META-INF/container.xml"
    ) -> bytes:
        names = list(docs) + (["nav.xhtml"] if nav else [])
        items = "".join(
            f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"'
            + (' properties="nav"/>' if n == "nav.xhtml" else "/>")
            for i, n in enumerate(names)
        )
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = f"<package><metadata><dc:title>Т</dc:title></metadata><manifest>{items}</manifest><spine>{spine}</spine></package>"
        files = [
            (cname, container.encode(), zipfile.ZIP_DEFLATED),
            ("OEBPS/content.opf", opf.encode(), zipfile.ZIP_DEFLATED),
        ]
        for n, body in docs.items():
            data = body if isinstance(body, bytes) else (head + body + "</body></html>").encode()
            files.append(("OEBPS/" + n, data, zipfile.ZIP_DEFLATED))
        if nav:
            files.append(
                ("OEBPS/nav.xhtml", (head + f'<nav epub:type="toc"><ol>{nav}</ol></nav></body></html>').encode(), 8)
            )
        return zipped(files + [(n, d, zipfile.ZIP_DEFLATED) for n, d in more or []])

    nfd = unicodedata.normalize("NFD", "рйс½.png")
    pics = epub(
        {
            "a.xhtml": "<p>Раз <img src='i1/pic.png'/> два <img src='i2/pic.png'/> три <img src='i1/pic.png'/>.</p>"
            f"<p>Имена <img src='i3/{nfd}'/> <img src='i3/a b+c.png'/> <img src='i3/Ке\u0301ды.png'/> "
            "<img src='i3/PIC.png'/>.</p>"
        },
        [("OEBPS/i1/pic.png", PNG), ("OEBPS/i2/pic.png", JPG), (f"OEBPS/i3/{nfd}", PNG + b"1"),
         ("OEBPS/i3/a b+c.png", PNG + b"2"), ("OEBPS/i3/Ке\u0301ды.png", PNG + b"3"), ("OEBPS/i3/PIC.png", PNG + b"4")],
    )  # fmt: skip
    text = "<html><body><p>Привет, мир.</p></body></html>"
    charsets = epub(
        {
            "a.xhtml": ('<?xml version="1.0" encoding="windows-1251"?>' + text).encode("cp1251"),
            "b.xhtml": '<html><head><meta charset="koi8-r"/></head><body><p>Ёлка.</p></body></html>'.encode("koi8_r"),
            "c.xhtml": b"\xff\xfe" + "<html><body><p>Шестнадцать.</p></body></html>".encode("utf-16-le"),
            "d.xhtml": b"\xfe\xff" + "<html><body><p>Наоборот.</p></body></html>".encode("utf-16-be"),
            "e.xhtml": b"\xef\xbb\xbf" + "<p>С меткой.</p>".encode(),
            "f.xhtml": '<?xml version="1.0" encoding="gb2312"?><p>Неизвестная — значит UTF-8.</p>'.encode(),
            "g.xhtml": "<meta http-equiv='Content-Type' content='text/html; charset=windows-1251'><p>Ещё 1251 \x98.</p>"
            .encode("cp1251", "replace").replace(b"?", b"\x98"),
        },
        cname="meta-inf/Container.XML",
    )  # fmt: skip
    big = bytearray(epub({"a.xhtml": "<p>x</p>"}, [("OEBPS/big.bin", b"0")]))
    cd = big.rindex(b"PK\x01\x02")  # the last entry's central record: its size field says 600 MB
    big[cd + 24 : cd + 28] = (600_000_000).to_bytes(4, "little")
    nfc = unicodedata.normalize("NFC", "глава й.xhtml")
    names = bytearray(
        zipped(
            [
                ("META-INF/container.xml", container.encode(), 8),
                ("OEBPS/content.opf", (f"<package><metadata><dc:title>Т</dc:title></metadata><manifest><item id='a' "
                 f"href='{nfc}' media-type='application/xhtml+xml'/><item id='b' href='b.xhtml' media-type='application/"
                 "xhtml+xml'/></manifest><spine><itemref idref='a'/><itemref idref='b'/></spine></package>").encode(), 8),
                ("OEBPS/" + unicodedata.normalize("NFD", nfc), "<p>Найдена.</p>".encode(), 8),
                ("OEBPS/b.xhtml", "<p>Первая запись.</p>".encode(), 8),
                ("OEBPS/c.xhtml", "<p>Последняя запись.</p>".encode(), 8),
            ]
        )
    )  # fmt: skip
    for at in (names.index(b"OEBPS/c.xhtml"), names.rindex(b"OEBPS/c.xhtml")):  # c: one more b, in both headers
        names[at + 6] = ord("b")
    sub = "<h3>Часть</h3><p>Т.</p>" * 4
    return [
        ("pustaya", epub({"a.xhtml": ""})),
        (
            "snoski",
            epub(
                {
                    "a.xhtml": "<p><sup><a href='#n2'>2</a></sup></p><p>Текст<a href='#n1'><sup>1</sup></a> и"
                    "<a href='#n3' id='r3'><sup>3</sup></a>.</p><p><a href='#n4'><sup>4</sup></a></p><p>Конец.</p>"
                    "<aside epub:type='footnote' id='n1'><p>...и далее по тексту.</p></aside>"
                    "<aside epub:type='footnote' id='n2'><p>Вторая.</p></aside>"
                    "<div class='footnote' id='n3'><p><a href='#r3'>3</a>. Третья.</p></div>"
                    "<aside epub:type='footnote' id='n4'><p>Четвёртая.</p></aside>"
                    "<aside role='doc-footnote' id='x'><p>Ни на что не указывает.</p></aside>"
                }
            ),
        ),
        ("kartinki", pics),
        (
            "zagolovki",
            epub(
                {"a.xhtml": "<h1>Один</h1><p>Раз.</p><h2><img src='x.png'/></h2><h2> </h2><p>Два.</p>"},
                [("OEBPS/x.png", PNG)],
            ),
        ),  # fmt: skip
        (
            "bloki",
            epub(
                {
                    "a.xhtml": "<pre>строка один\n  строка два<div>див\nвнутри</div>хвост\nконец</pre>"
                    "<p>Москва, ул. Тверская, 1,<br/>Ивану Петрову</p><p>Ваш,<br/>И. П.,<br/>1900</p>"
                    "<p>Раз,<br/>два,<br/>три,<br/>четыре.</p><div class='poem'><p>Стих,<br/>второй.</p></div>"
                    "<ol><li>A. Первый.</li><li>I. Второй.</li><li>— Третий.</li><li>4. Четвёртый.</li></ol>"
                    "<ul><li>— реплика</li><li>• уже с точкой</li></ul>"
                    "<ol start='9223372036854775807'><li>а</li><li>б</li></ol><ol start=' -2 '><li>в</li><li>г</li>"
                    f"<li>д</li></ol><ol><li value='007'>е</li><li>ж</li></ol><ol start='{'9' * 101}'><li>з</li></ol>"
                    "<ol start='99999999999999999999'><li>и</li><li>к</li></ol>"
                }
            ),
        ),
        ("kodirovki", charsets),
        (
            "oglavlenie-glavy",
            epub(
                {
                    **{f"{d}.xhtml": f"<h2>Глава {d}</h2>{sub}" for d in "abc"},
                    "n.xhtml": "<section epub:type='endnotes'>" + "<h3>Примечание</h3><p>П.</p>" * 9 + "</section>",
                },
                nav="".join(f"<li><a href='{d}.xhtml'>Глава {d}</a></li>" for d in "abc"),
            ),
        ),
        (
            "oglavlenie-faily",
            epub(
                {
                    "t.xhtml": "<p>Титул.</p>",
                    "b.xhtml": "<h2>Книга</h2><p>Автор.</p>"
                    + "".join(f"<h3>{i}</h3><p>Текст {i}.</p>" for i in range(1, 8)),
                },
                nav="<li><a href='t.xhtml'>Титул</a></li><li><a href='b.xhtml'>Книга</a></li>",
            ),
        ),  # fmt: skip
        (
            "oglavlenie-malo",
            epub(
                {f"{d}.xhtml": f"<h1>Глава {d}</h1><p>Т.</p>" for d in "abcde"},
                nav="<li><a href='a.xhtml'>А</a></li><li><a href='b.xhtml'>Б</a></li>",
            ),
        ),  # fmt: skip
        (
            "kavychki",
            epub(
                {
                    "a.xhtml": "<p>Слово<sup class='note'><a href='#n1'>1</a></sup> и ещё.</p>"
                    "<p>Знак<sup class='note'>2</sup> остался.</p>"
                    "<p>Он сказал <a epub:type='noteref' href='#n1'>1</a>\"Привет\" и ушёл.</p>"
                    "<p><b>Слово <a epub:type='noteref' href='#n1'>2</a>\"</b> и <em>дальше</em>.</p>"
                    "<p>Он <a epub:type='noteref' href='#n1'>3</a>'тут' и <i>там <a epub:type='noteref' href='#n1'>4</a>'"
                    "</i></p><p>Конец <a epub:type='noteref' href='#n1'>5</a>\"</p>"
                    "<aside epub:type='footnote' id='n1'><p>Сноска.</p></aside>"
                }
            ),
        ),
        ("bolshoy", bytes(big)),
        ("imena", bytes(names)),
        (
            "glubina",
            epub(
                {
                    "a.xhtml": "<p>Начало.</p>"
                    + "<div><span>" * 400
                    + "слово " * 50
                    + "</span></div>" * 400
                    + "<p>Середина.</p>",
                    "b.xhtml": "<p>слово" * 3000,
                }
            ),
        ),  # fmt: skip
    ]


def extract_epub_bytes(name: str, data: bytes) -> dict:
    case: dict = {"name": name, "data": base64.b64encode(data).decode()}
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / (name + ".epub")
        src.write_bytes(data)
        try:
            case["book"] = dump_book(extract_epub.extract(src))
        except SystemExit as e:
            case["error"] = str(e)
            return case
        images = Path(tmp) / "images"
        case["images"] = (
            {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(images.iterdir())}
            if images.is_dir()
            else {}
        )
    return case


def _nested(n: int, tag: str, inner: str) -> str:
    return "<div>" + f"<{tag}>" * n + inner + f"</{tag}>" * n + "хвост</div>"


# XHTML as html.parser and BeautifulSoup read it (extract_epub.parse, its deep nesting flattened): `markup.json`
# holds each with its tree, which the phone's HTMLText.parse is held to node by node.
MARKUP = [
    "<p>a &alpha; &ampx; &copy2024 &a; &notin; &fjlig; &Alpha; &hearts; &NBSP; &amp &lt; &gt &nbsp &a-b; &a.b</p>",
    "<p>&#65;&#x41;&#X41g &#0; &#1; &#11; &#x7f; &#xFFFE; &#x80; &#x81; &#150; &#xD800; &#99999999999; &#x1F600;</p>",
    "<p>&#65a</p><p>Это уже текст.</p>",
    "<p>x &#; y &#; z</p><p>Текст после.</p>",
    "<p>Начало <!-- не закрыт</p><p>Дальше текст.</p>",
    '<p a="unterminated>x</p><p>y</p>',
    '<p a= "x>y</p><p a=="x>z</p>',
    "a</ b>c</3>d</>e</b c='d'>f<b>g</B >h",
    "<p\xa0class=x>t</p><p\x0bclass=y>u</p><p\tclass=z>v</p>",
    '<a xlink:href="1" l:href="2" href="3">x</a><a b=1 b=2 c d=>y</a><p title="&alpha;&notin &notit; &#1;">z</p>',
    "<p>x<!DOCTYPE html>y<?php echo ?>z<![CDATA[ c ]]>w<!x>v<!>u</p><![if !x]>a<![endif]>b",
    "<!--a--b-->c<!---->d<!-- x --!>e<!--->f",
    "<p>x<br>y<br/>z</br>w<img src=a.png>v</img>u<br></br>t</p>",
    '<script>if (a<b) { x = "</p>"; }</script><p>t</p><style>p{}</ style >q</style>r',
    "<p>  <em>x</em>   </p><pre>  \n  </pre><p>\n  \n</p><ruby>漢<rt>kan</rt><rp>(</rp></ruby><template>t</template>",
    '<p class="  a   b ">x</p><p/>x<div/>y<span />z<div><p>a<div>b</p>c</div>d<table><tr><td>a<td>b</table>',
    '<a href=x/y>t</a><a href=x/>u</a><a / href="x">v</a><a\nhref\n=\n"x"\n>w</a><a href="x"title="y">s</a>',
    "< p>x <3 y<p>a < b > c</p>tail &a",
    _nested(99, "span", "x"),
    _nested(101, "span", "x"),
    _nested(150, "i", "a<br>b<img src=x>c<p>d</p>"),
    "<p>" * 130 + "конец",
    "<div>" * 120 + "<p>a<!--c-->b</p><span></span><em><b></b></em><br>" + "</div>" * 120,
    "<div>" * 105 + "<style>s</style><script></script>x<pre>  </pre> <textarea>\n</textarea>",
    "<script>never ends",
]


def markup_tree(node) -> list:
    """A parsed node's children: text (one string for neighbours), ["#comment", s], ["#raw", s] for the strings
    bs4 does not count as text (script, style, ruby text, a template's), [name, [[attr, value]...], children];
    doctypes, declarations and processing instructions are left out."""
    from bs4 import CData, Comment, NavigableString, Tag

    out: list = []
    for c in node.contents:
        if isinstance(c, Tag):
            attrs = [[k, " ".join(v) if isinstance(v, list) else v] for k, v in c.attrs.items()]
            out.append([c.name, attrs, markup_tree(c)])
        elif type(c) in (NavigableString, CData):
            if out and isinstance(out[-1], str):
                out[-1] += str(c)
            else:
                out.append(str(c))
        elif type(c) is Comment:
            out.append(["#comment", str(c)])
        elif type(c).__name__ in ("Script", "Stylesheet", "TemplateString", "RubyTextString", "RubyParenthesisString"):
            if out and isinstance(out[-1], list) and len(out[-1]) == 2 and out[-1][0] == "#raw":
                out[-1][1] += str(c)
            else:
                out.append(["#raw", str(c)])
    return out


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    cases = []
    for name, data, opts in sources():
        files[name] = data
        case, book = expect(name, data)
        case.update(opts)
        if book is not None:
            case["book"] = name + ".book.json"
            files[case["book"]] = book
        cases.append(case)
    files["expected.json"] = (json.dumps(cases, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    txt = [
        {"name": n, "data": base64.b64encode(d).decode(), "book": dump_book(extract_txt_bytes(n, d))}
        for n, d in txt_cases()
    ]
    files["txt.json"] = (json.dumps(txt, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    pdf = [
        {
            "name": n,
            "pages": [[list(ln) for ln in pg] for pg in pages],
            "book": dump_book(
                extract_pdf.book_from_lines([[extract_pdf.Line(*ln) for ln in pg] for pg in pages], n, "")
            ),
        }
        for n, pages in pdf_line_cases()
    ]
    files["pdf.json"] = (json.dumps(pdf, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    epubs = [extract_epub_bytes(n, d) for n, d in epub_cases()]
    files["epub.json"] = (json.dumps(epubs, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    sentences = [{"text": t, "sentences": split_sentences(t)} for t in SENTENCES]
    files["sentences.json"] = (json.dumps(sentences, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    files["model.book.json"] = dump_book(model_book()).encode("utf-8")
    markup = [{"html": h, "tree": markup_tree(extract_epub.parse(h.encode("utf-8")))} for h in MARKUP]
    files["markup.json"] = (json.dumps(markup, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return files


def main() -> None:
    check = "--check" in sys.argv[1:]
    files = build()
    have = {f.name: f.read_bytes() for f in OUT.iterdir()} if OUT.is_dir() else {}
    stale = sorted(n for n in files.keys() | have.keys() if files.get(n) != have.get(n))
    if check:
        if stale:
            sys.exit("stale import vectors: " + ", ".join(stale) + "; run tests/make_import_vectors.py")
        print(f"import vectors up to date ({len(files)} files)")
        return
    OUT.mkdir(exist_ok=True)
    for n in stale:
        if n in files:
            (OUT / n).write_bytes(files[n])
        elif (OUT / n).is_dir():
            shutil.rmtree(OUT / n)
        else:
            (OUT / n).unlink()
    print(f"{len(files)} files, {len(stale)} written or removed: {', '.join(stale) or 'none'}")


if __name__ == "__main__":
    main()
