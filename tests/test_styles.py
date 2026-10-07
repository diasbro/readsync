"""The book's own look (book.json's `st`) is an addition and nothing else: with or without it every book has
the same text, spans, chapters and notes. And the look itself: alignment, indents, margins, blank lines."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))

import extract_epub  # noqa: E402
import extract_style  # noqa: E402
import extract_text  # noqa: E402
from extract_style import block_style, declarations, rules, tenths  # noqa: E402

spec = importlib.util.spec_from_file_location("make_import_vectors", HERE / "make_import_vectors.py")
assert spec and spec.loader
vectors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vectors)

HTML = """<html><head><title>Проба</title><meta name="author" content="Иван Иванов"></head><body>
<article id="book-content">
<section id="s1"><h2><p>Глава первая</p></h2>
<div class="epigraph"><p>Эпиграф.</p><p class="text-author">Автор</p></div>
<p>Первый абзац. Второе предложение.</p>
<div class="empty-line"></div><div class="empty-line"></div>
<p>Второй абзац после пустых строк.</p>
<div class="poem"><div class="stanza"><p>Строка раз</p><p>Строка два</p></div></div>
<div class="empty-line"></div>
</section>
<section id="s2"><h2><p>Глава вторая</p></h2><div class="empty-line"></div><p>Текст.</p></section>
</article></body></html>"""


def without_st(book: dict) -> dict:
    return {**book, "blocks": [{k: v for k, v in b.items() if k != "st"} for b in book["blocks"]]}


def books(monkeypatch, enabled: bool) -> dict[str, dict]:
    monkeypatch.setattr(extract_style, "ENABLED", enabled)
    out = {}
    for name, data, opts in vectors.sources():
        if "error" in opts:
            continue
        _, book = vectors.expect(name, data)
        out[name] = json.loads(book)
    return out


def test_vectors_have_the_same_text_with_and_without_styles(monkeypatch):
    styled, plain = books(monkeypatch, True), books(monkeypatch, False)
    assert styled.keys() == plain.keys() and len(styled) >= 11
    for name in styled:
        a, b = styled[name], plain[name]
        assert not any("st" in blk for blk in b["blocks"]), name
        assert [blk["text"] for blk in a["blocks"]] == [blk["text"] for blk in b["blocks"]], name
        assert [blk["sentences"] for blk in a["blocks"]] == [blk["sentences"] for blk in b["blocks"]], name
        assert a["chapters"] == b["chapters"], name
        assert without_st(a) == b, name  # everything else as well: kinds, ids, em, notes, images
    # the styled books do carry styles, and the plain old ones carry none
    assert any("st" in blk for blk in styled["stil.epub"]["blocks"])
    assert any("st" in blk for blk in styled["stihi.fb2"]["blocks"])
    for name in ("kniga.epub", "zapiska.epub", "zapiski.txt", "rasskaz-1251.fb2", "doklad.pdf"):
        assert not any("st" in blk for blk in styled[name]["blocks"]), name


def test_html_page_has_the_same_text_with_and_without_styles(monkeypatch, tmp_path):
    src = tmp_path / "book.html"
    src.write_text(HTML, encoding="utf-8")
    styled = extract_text.extract(src)
    monkeypatch.setattr(extract_style, "ENABLED", False)
    plain = extract_text.extract(src)
    assert without_st(styled) == plain
    gaps = [(b["text"], b.get("st")) for b in styled["blocks"] if "st" in b]
    # two empty lines before the second paragraph; the one closing a chapter and the one after a title
    assert gaps == [("Второй абзац после пустых строк.", {"g": 2}), ("Текст.", {"g": 1})]


def test_epub_styles(tmp_path):
    src = tmp_path / "stil.epub"
    src.write_bytes(vectors.epub_stil())
    book = extract_epub.extract(src)
    blocks = book["blocks"]
    first = next(b for b in blocks if b["kind"] == "p")
    assert first.get("st") == {"a": "j", "i": 0}  # .first: no indent, the body's justify inherited
    centered = [b for b in blocks if b["kind"] == "p" and b.get("st", {}).get("a") == "c"]
    assert centered and centered[0]["text"] == "* * *" and centered[0]["st"] == {"a": "c", "i": 0}
    verse = [b for b in blocks if b["kind"] == "verse"]
    assert [b.get("st") for b in verse] == [{"m": 2}, {"m": 2}]  # left is a verse's own, not repeated
    epi = next(b for b in blocks if b["kind"] == "epigraph")
    assert epi["st"] == {"a": "l", "i": 1.5, "m": 10}  # 40% clamped to 10em
    titles = [b for b in blocks if b["kind"] in ("title", "subtitle")]
    # h1/h2 are centered by the book and by their kind: nothing to say. h3 is left with a margin, h4 right
    assert [b.get("st") for b in titles] == [None, {"a": "l", "m": 3}, {"a": "r"}, None, None]
    gaps = [b["st"]["g"] for b in blocks if "g" in b.get("st", {})]
    assert gaps == [1, 2]  # &nbsp; once, then two empty ones; none before a chapter or across documents


@pytest.mark.parametrize(
    "value,want",
    [
        ("1.5em", 15),
        (".25em", 3),
        ("2rem", 20),
        ("24px", 15),
        ("12pt", 10),
        ("10%", 30),
        ("0", 0),
        ("3", None),
        ("-1em", None),
        ("auto", None),
        ("1e3px", None),
        ("99999999999999999999em", 1000000),
    ],
)
def test_tenths(value, want):
    assert tenths(value) == want


def test_declarations_and_rules():
    assert declarations("TEXT-ALIGN: Center !important; margin: 0 1em 0 2em; padding: 3px 8px") == {
        "a": "c",
        "ml": 20,
        "pl": 5,
    }
    assert declarations("text-align: middle; text-indent: -2em; margin-left: auto; color: red") == {}
    assert declarations("margin: 1em 2em 3em") == {"ml": 20}
    css = "@charset 'x'; /* p { text-align: right } */ p{a:b} @media x { p { c: d } } .x { e: f }"
    assert rules(css) == [("p", "a:b"), (".x", " e: f ")]


def test_block_style_leaves_out_what_the_kind_says():
    assert block_style("title", {"a": "c"}, {}) is None
    assert block_style("title", {}, {"a": "j", "i": 15}) is None  # a heading does not inherit the body's look
    assert block_style("author", {"a": "r", "i": 0}, {}) is None
    assert block_style("p", {"i": 0}, {}) == {"i": 0}
    assert block_style("cite", {"i": 0}, {}) is None
    assert block_style("p", {"ml": 10, "pl": 5}, {"a": "r"}) == {"a": "r", "m": 1.5}
    assert block_style("p", {}, {}, gap=7) == {"g": 3}
    assert block_style("p", {}, {}) is None


def test_rules_keep_braces_in_strings_and_drop_comments():
    """X48: a `}` or `;` in a quoted string is the string's, a comment goes, an unclosed string ends at its line."""
    css = 'a.x::after { content: "}"; } p.c { text-align: center; } p.i { text-indent: 2em }'
    assert rules(css) == [
        ("a.x::after", ' content: "}"; '),
        ("p.c", " text-align: center; "),
        ("p.i", " text-indent: 2em "),
    ]
    css = '@font-face { src: url("a}.ttf"); } /* p { */ p.d{margin:1em} q{content:"\\"}"} r{content:\'/* x */\'}'
    assert rules(css) == [("p.d", "margin:1em"), ("q", 'content:"\\"}"'), ("r", "content:'/* x */'")]
    assert rules('p{content:"open\n} q{text-align:right}') == [("p", 'content:"open\n'), ("q", "text-align:right")]
