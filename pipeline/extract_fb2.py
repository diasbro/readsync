"""Extract an FB2 (or .fb2.zip) file into the same book.json model as extract_text.py."""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import split_sentences  # noqa: E402

NS = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0", "l": "http://www.w3.org/1999/xlink"}


def tag(el) -> str:
    return el.tag.split("}", 1)[-1]


def read_fb2(path: Path) -> bytes:
    if path.suffix == ".zip" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".fb2"))
            return z.read(name)
    return path.read_bytes()


def inline(el) -> tuple[str, list[list[int]], list[dict]]:
    parts: list[str] = []
    em: list[list[int]] = []
    notes: list[dict] = []
    pos = 0

    def add(s: str, in_em: bool):
        nonlocal pos
        if not s:
            return
        if in_em:
            if em and em[-1][1] == pos:
                em[-1][1] = pos + len(s)
            else:
                em.append([pos, pos + len(s)])
        parts.append(s)
        pos += len(s)

    def walk(node, in_em: bool):
        t = tag(node)
        if t == "a" and node.get("type") == "note":
            href = node.get("{{{}}}href".format(NS["l"]), "") or node.get("href", "")
            notes.append({"pos": pos, "id": href.lstrip("#")})
            add(node.tail or "", in_em)
            return
        child_em = in_em or t in ("emphasis", "i")
        add(node.text or "", child_em)
        for c in node:
            walk(c, child_em)
        add(node.tail or "", in_em)

    add(el.text or "", False)
    for c in el:
        walk(c, False)
    text = "".join(parts).replace("\xa0", " ")
    lead = len(text) - len(text.lstrip(" "))
    body = re.sub(r"\s+", " ", text[lead:]).strip()
    new = " " * lead + body
    if new != text:
        from extract_text import build_offset_map

        m = build_offset_map(text, new)
        em = [[m[a], m[b]] for a, b in em if m[b] > m[a]]
        notes = [{"pos": m[n["pos"]], "id": n["id"]} for n in notes]
    return new, em, notes


def extract(path: Path) -> dict:
    root = ET.fromstring(read_fb2(path))
    desc = root.find("fb:description/fb:title-info", NS)
    title = desc.findtext("fb:book-title", default=path.stem, namespaces=NS) if desc is not None else path.stem
    author = ""
    if desc is not None and desc.find("fb:author", NS) is not None:
        a = desc.find("fb:author", NS)
        author = " ".join(x for x in (a.findtext("fb:first-name", "", NS), a.findtext("fb:last-name", "", NS)) if x)
    blocks: list[dict] = []
    chapters: list[dict] = []
    notes: dict[str, str] = {}
    stanza_n = 0

    def add_block(el, kind, ch, audio=True, stanza=None, bid=None):
        text, em, nrefs = inline(el)
        if not text.strip():
            return
        blocks.append(
            {
                "id": bid or el.get("id") or f"b{len(blocks)}",
                "kind": kind,
                "chapter": ch,
                "stanza": stanza,
                "text": text,
                "em": em,
                "notes": nrefs,
                "sentences": split_sentences(text) if kind == "p" else [[0, len(text)]],
                "audio": audio,
            }
        )

    def handle(el, ch, kind_override=None, stanza=None):
        nonlocal stanza_n
        t = tag(el)
        if t == "section":
            walk_section(el, chapters[ch]["level"] + 1)
        elif t == "title":
            for p in el:
                add_block(p, "title", ch)
        elif t == "subtitle":
            add_block(el, "subtitle", ch)
        elif t == "p":
            add_block(el, kind_override or "p", ch, stanza=stanza)
        elif t == "v":
            add_block(el, "verse", ch, stanza=stanza)
        elif t == "poem":
            for c in el:
                if tag(c) == "stanza":
                    stanza_n += 1
                    for v in c:
                        handle(v, ch, stanza=stanza_n)
                elif tag(c) == "text-author":
                    add_block(c, "author", ch)
                elif tag(c) == "title":
                    for p in c:
                        add_block(p, "subtitle", ch)
        elif t == "epigraph":
            for c in el:
                handle(c, ch, kind_override="author" if tag(c) == "text-author" else "epigraph")
        elif t == "cite":
            for c in el:
                handle(c, ch, kind_override="author" if tag(c) == "text-author" else "cite")
        elif t == "text-author":
            add_block(el, "author", ch)
        elif t in ("empty-line", "image", "table"):
            return
        else:
            for c in el:
                handle(c, ch, kind_override, stanza)

    def walk_section(sec, level):
        ttl = sec.find("fb:title", NS)
        title_txt = " ".join(re.sub(r"\s+", " ", "".join(p.itertext())).strip() for p in ttl) if ttl is not None else ""
        ch = len(chapters)
        chapters.append(
            {"id": sec.get("id") or f"s{ch}", "title": title_txt.strip(), "level": level, "first_block": len(blocks)}
        )
        for c in sec:
            handle(c, ch)

    bodies = root.findall("fb:body", NS)
    for body in bodies:
        if body.get("name") in ("notes", "comments"):
            for sec in body.findall(".//fb:section", NS):
                nid = sec.get("id")
                if not nid:
                    continue
                [
                    p
                    for p in sec.iter()
                    if tag(p) == "p" and (tag(p.getparent()) if hasattr(p, "getparent") else "") != "title"
                ]
                txt = " ".join(re.sub(r"\s+", " ", "".join(p.itertext())).strip() for p in sec.findall("fb:p", NS))
                notes[nid] = txt
            continue
        bt = body.find("fb:title", NS)
        if bt is not None:
            chapters.append(
                {
                    "id": "body",
                    "title": " ".join("".join(p.itertext()).strip() for p in bt),
                    "level": 1,
                    "first_block": len(blocks),
                }
            )
            for p in bt:
                add_block(p, "title", len(chapters) - 1)
        for c in body:
            if tag(c) == "section":
                walk_section(c, 1)
            elif tag(c) != "title":
                handle(c, len(chapters) - 1 if chapters else 0)
    return {"title": title, "author": author, "chapters": chapters, "blocks": blocks, "notes": notes}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.fb2 / book.fb2.zip -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.name.lower().endswith((".fb2", ".fb2.zip"))), None)
    if src is None:
        sys.exit("no .fb2 file in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    print(
        f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} "
        f"words={sum(len(b['text'].split()) for b in book['blocks'])} notes={len(book['notes'])}"
    )


if __name__ == "__main__":
    main()
