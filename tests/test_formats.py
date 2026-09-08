import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from extract_epub import extract as extract_epub  # noqa: E402
from extract_text import extract as extract_html  # noqa: E402
from extract_txt import extract as extract_txt  # noqa: E402
from merge_books import merge  # noqa: E402


def test_epub(tmp_path):
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<package><metadata><dc:title>Проба</dc:title><dc:creator>Автор</dc:creator><meta name="cover" content="cov"/></metadata>'
            '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="cov" href="cover.jpg" media-type="image/jpeg"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>',
        )
        z.writestr(
            "OEBPS/ch1.xhtml",
            "<html><body><h1>Глава первая</h1><p>Первое. Второе <em>курсив</em>.</p><img src='pic.png'/><p>Третье.</p></body></html>",
        )
        z.writestr("OEBPS/ch2.xhtml", "<html><body><h2>Глава вторая</h2><p>Текст.</p></body></html>")
        z.writestr("OEBPS/pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 8)
        z.writestr("OEBPS/cover.jpg", b"\xff\xd8\xff" + b"0" * 8)
    book = extract_epub(p)
    assert book["title"] == "Проба" and book["author"] == "Автор"
    assert [c["title"] for c in book["chapters"]] == ["Глава первая", "Глава вторая"]
    kinds = [b["kind"] for b in book["blocks"]]
    assert kinds == ["title", "p", "p", "title", "p"]
    assert book["blocks"][2]["images"] == [{"src": "images/pic.png"}]
    assert (tmp_path / "images" / "cover.jpg").exists()
    assert len(book["blocks"][1]["sentences"]) == 2


def test_txt(tmp_path):
    p = tmp_path / "book.txt"
    p.write_text(
        "Часть первая\n\nГлава 1\n\nПервый абзац. Ещё одно предложение.\n\nВторой абзац.\n\nГЛАВА 2\n\nТекст второй главы.\n",
        encoding="utf-8",
    )
    book = extract_txt(p)
    assert [c["title"] for c in book["chapters"]] == ["Часть первая", "Глава 1", "ГЛАВА 2"]
    assert [b["kind"] for b in book["blocks"]] == ["title", "title", "p", "p", "title", "p"]
    assert len(book["blocks"][2]["sentences"]) == 2


def test_txt_cp1251(tmp_path):
    p = tmp_path / "book.txt"
    p.write_bytes("Глава 1\n\nТекст.\n".encode("cp1251"))
    assert extract_txt(p)["blocks"][1]["text"] == "Текст."


def test_generic_html(tmp_path):
    p = tmp_path / "book.html"
    p.write_text(
        "<html><head><title>Страница</title></head><body><nav><p>меню меню меню</p></nav>"
        "<div id='main'><h2>Глава I</h2><p>Первый абзац текста книги. Второе предложение.</p><p>Второй абзац.</p></div></body></html>",
        encoding="utf-8",
    )
    book = extract_html(p)
    assert book["title"] == "Страница"
    assert [b["kind"] for b in book["blocks"]] == ["title", "p", "p"]
    assert book["chapters"][0]["title"] == "Глава I"


def test_merge_parts():
    a = {
        "title": "Т. 1",
        "author": "А",
        "chapters": [{"id": "s1", "title": "Гл 1", "level": 1, "first_block": 0}],
        "blocks": [
            {
                "images": [],
                "id": "p1",
                "kind": "p",
                "chapter": 0,
                "stanza": None,
                "text": "x",
                "em": [],
                "notes": [{"pos": 0, "id": "n1"}],
                "sentences": [[0, 1]],
                "audio": True,
            }
        ],
        "notes": {"n1": "сноска"},
    }
    b = {
        "title": "Т. 2",
        "author": "А",
        "chapters": [{"id": "s1", "title": "Гл 2", "level": 1, "first_block": 0}],
        "blocks": [
            {
                "images": ["images/i.png"],
                "id": "p1",
                "kind": "p",
                "chapter": 0,
                "stanza": None,
                "text": "y",
                "em": [],
                "notes": [],
                "sentences": [[0, 1]],
                "audio": True,
            }
        ],
        "notes": {},
    }
    m = merge([("Том 1", a), ("Том 2", b)], title="Книга")
    assert m["title"] == "Книга"
    assert [(c["title"], c["level"], c["first_block"]) for c in m["chapters"]] == [
        ("Том 1", 1, 0),
        ("Гл 1", 2, 1),
        ("Том 2", 1, 2),
        ("Гл 2", 2, 3),
    ]
    assert [b["chapter"] for b in m["blocks"]] == [0, 1, 2, 3]
    assert m["blocks"][1]["notes"] == [{"pos": 0, "id": "p1_n1"}] and m["notes"] == {"p1_n1": "сноска"}
    assert m["blocks"][3]["images"] == [{"src": "images/i.png"}]


def test_a_stub_page_is_not_taken_for_a_book():
    """Catalogs answer a blocked book with a notice; loading one must stop, not make a five-page book."""
    import pytest
    from add_book import check_real_book

    def book(*paragraphs):
        return {"blocks": [{"text": p} for p in paragraphs]}

    blocked = book("Книга заблокирована.", "Книга заблокирована по одной из причин: жалоба правообладателя.")
    with pytest.raises(SystemExit):
        check_real_book(blocked, 1)
    with pytest.raises(SystemExit):  # no notice, just nothing to read
        check_real_book(book("Одна короткая страница."), 1)
    check_real_book(book(" ".join(["слово"] * 600)), 1)  # a real book passes
