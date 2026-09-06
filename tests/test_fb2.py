import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from extract_fb2 import extract  # noqa: E402

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
    assert p["notes"] == [{"pos": len(p["text"]) - 1, "id": "n1"}]
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
