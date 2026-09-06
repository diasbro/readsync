"""Extract an EPUB into the book.json model (same shape as extract_text.py)."""

from __future__ import annotations

import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import inline_text, split_sentences  # noqa: E402

HEADINGS = {"h1": 1, "h2": 2, "h3": 3}


def opf_path(z: zipfile.ZipFile) -> str:
    container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    m = re.search(r'full-path="([^"]+)"', container)
    if not m:
        raise SystemExit("EPUB without OPF")
    return m.group(1)


def spine_docs(z: zipfile.ZipFile) -> tuple[list[str], dict, str]:
    """Return (ordered content documents, metadata, opf directory)."""
    opf = opf_path(z)
    base = posixpath.dirname(opf)
    xml = z.read(opf).decode("utf-8", "replace")
    items = {}
    for m in re.finditer(r"<item\b([^>]*)/?>", xml):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(1)))
        if "id" in attrs and "href" in attrs:
            items[attrs["id"]] = attrs
    order = re.findall(r'<itemref\b[^>]*idref="([^"]+)"', xml)
    docs = []
    for idref in order:
        it = items.get(idref)
        if it and ("html" in it.get("media-type", "") or it["href"].lower().endswith((".xhtml", ".html", ".htm"))):
            docs.append(posixpath.normpath(posixpath.join(base, it["href"])))
    meta = {
        "title": _tag(xml, "dc:title"),
        "author": _tag(xml, "dc:creator"),
        "language": _tag(xml, "dc:language"),
    }
    cover_id = re.search(r'<meta\b[^>]*name="cover"[^>]*content="([^"]+)"', xml)
    cover = items.get(cover_id.group(1)) if cover_id else None
    if cover is None:
        cover = next((it for it in items.values() if "cover-image" in it.get("properties", "")), None)
    meta["cover"] = posixpath.normpath(posixpath.join(base, cover["href"])) if cover else None
    return docs, meta, base


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", xml, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def extract(path: Path) -> dict:
    z = zipfile.ZipFile(path)
    docs, meta, base = spine_docs(z)
    blocks: list[dict] = []
    chapters: list[dict] = []
    pending_images: list[dict] = []
    img_dir = path.parent / "images"
    names = set(z.namelist())

    def add_block(el: Tag, kind: str, audio: bool = True):
        text, em, nrefs = inline_text(el)
        if not text.strip():
            return
        if not chapters:
            chapters.append({"id": "s0", "title": "", "level": 1, "first_block": 0})
        blocks.append(
            {
                "images": pending_images.copy(),
                "id": f"b{len(blocks)}",
                "kind": kind,
                "chapter": len(chapters) - 1,
                "stanza": None,
                "text": text,
                "em": em,
                "notes": [],
                "sentences": split_sentences(text) if kind == "p" else [[0, len(text)]],
                "audio": audio,
            }
        )
        pending_images.clear()

    def save_image(src: str, doc: str) -> None:
        full = posixpath.normpath(posixpath.join(posixpath.dirname(doc), src))
        if full in names:
            img_dir.mkdir(exist_ok=True)
            name = re.sub(r"[^\w.-]+", "_", posixpath.basename(full))
            (img_dir / name).write_bytes(z.read(full))
            pending_images.append({"src": f"images/{name}"})

    def walk(el: Tag, doc: str):
        for c in el.children:
            if not isinstance(c, Tag):
                continue
            n = c.name
            if n in HEADINGS:
                title = re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                chapters.append(
                    {
                        "id": f"s{len(chapters)}",
                        "title": title,
                        "level": min(HEADINGS[n], 2),
                        "first_block": len(blocks),
                    }
                )
                add_block(c, "title")
            elif n == "p":
                cls = " ".join(c.get("class", []))
                kind = "verse" if re.search(r"verse|stanza|poem", cls) else "epigraph" if "epigraph" in cls else "p"
                add_block(c, kind)
            elif n in ("h4", "h5", "h6"):
                add_block(c, "subtitle")
            elif n == "blockquote":
                for p in c.find_all("p"):
                    add_block(p, "cite")
            elif n == "img":
                save_image(c.get("src", ""), doc)
            elif n == "image":
                save_image(c.get("xlink:href") or c.get("href", ""), doc)
            elif n in ("script", "style", "nav", "table"):
                continue
            else:
                walk(c, doc)

    for doc in docs:
        if doc not in names:
            continue
        soup = BeautifulSoup(z.read(doc).decode("utf-8", "replace"), "html.parser")
        body = soup.body or soup
        walk(body, doc)
    if meta.get("cover") and meta["cover"] in names:
        img_dir.mkdir(exist_ok=True)
        ext = posixpath.splitext(meta["cover"])[1].lower() or ".jpg"
        (img_dir / f"cover{ext}").write_bytes(z.read(meta["cover"]))
    return {
        "title": meta["title"] or path.stem,
        "author": meta["author"],
        "chapters": chapters,
        "blocks": blocks,
        "notes": {},
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.epub -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.suffix.lower() == ".epub"), None)
    if src is None:
        raise SystemExit("no .epub in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
