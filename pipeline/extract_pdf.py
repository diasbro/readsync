"""Extract a PDF into the book.json model. A PDF states lines, not paragraphs: running heads and
page numbers are dropped, words broken across lines are joined, and a paragraph ends where a short
line ends a sentence. Chapters are then guessed the same way as in a plain-text book."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_txt import HEADING, build, paragraphs  # noqa: E402

PAGE_NO = re.compile(r"^[\[\-–—\s]*(?:\d{1,4}|[ivxlcdm]{1,7}|стр\.?\s*\d{1,4})[\]\-–—\s.]*$", re.I)
ENDS_SENTENCE = re.compile(r"[.!?…:][»”\"')\]]*$")
REPEATS = 0.5  # a line on this share of the pages is a running head, not text


def heading(line: str) -> bool:
    """A line that names a chapter: it ends its own paragraph, however it is punctuated."""
    return (
        len(line) <= 80
        and len(line.split()) <= 8
        and not line.endswith((",", ";"))
        and bool(HEADING.match(line.rstrip(".")))
    )


def open_pdf(path: Path):
    """The file, opened once: an "owner" lock without a password opens with an empty one, a real
    password is not guessed."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise SystemExit("PDF под паролем: сними защиту (например, «Экспорт в PDF» в Просмотре) и добавь снова")
    return reader


def page_texts(reader) -> list[str]:
    return [p.extract_text() or "" for p in reader.pages]


def strip_furniture(pages: list[str]) -> list[list[str]]:
    """Page numbers and the running head or foot, the lines a page repeats from its neighbours."""
    stripped = [[ln.strip() for ln in p.replace("\xa0", " ").split("\n")] for p in pages]
    stripped = [[ln for ln in page if ln] for page in stripped]
    edges: dict[str, int] = {}
    for page in stripped:
        for ln in {ln for ln in (page[:1] + page[-1:])}:
            edges[re.sub(r"\d+", "#", ln.lower())] = edges.get(re.sub(r"\d+", "#", ln.lower()), 0) + 1
    running = {k for k, n in edges.items() if n >= max(3, len(stripped) * REPEATS)}
    out = []
    for page in stripped:
        keep = list(page)
        for i in (0, -1):
            while keep and (PAGE_NO.match(keep[i]) or re.sub(r"\d+", "#", keep[i].lower()) in running):
                keep.pop(i)
        out.append(keep)
    return out


def join_lines(pages: list[list[str]]) -> str:
    """One text: a word split by a hyphen at a line end is put back together, and a line that ends a
    sentence well before the right margin ends its paragraph."""
    lines = [ln for page in pages for ln in page]
    # the width of the text column: nearly the longest line, so a line that stops short of it ends a paragraph
    lens = sorted(len(ln) for ln in lines)
    width = lens[int(len(lens) * 0.9)] if lens else 0
    out: list[str] = []
    buf = ""
    for ln in lines:
        if heading(ln):  # a heading stands alone, it never runs into the paragraph under it
            if buf:
                out.append(buf)
            out.append(ln)
            buf = ""
            continue
        hyphenated = buf.endswith("-") and ln[:1].islower()
        buf = buf[:-1] + ln if hyphenated else f"{buf} {ln}".strip()
        short = len(ln) < width * 0.92
        if ENDS_SENTENCE.search(ln) and short:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return "\n\n".join(out)


def extract(path: Path) -> dict:
    reader = open_pdf(path)
    text = join_lines(strip_furniture(page_texts(reader)))
    if len(text.split()) < 10:  # a scan yields nothing at all; a short book is still a book
        raise SystemExit("в PDF нет текстового слоя (скан?): распознай его, например в Preview или ABBYY")
    meta = reader.metadata or {}
    title = (meta.get("/Title") or "").strip() or path.stem
    author = (meta.get("/Author") or "").strip()
    return build(paragraphs(text), title, author)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.pdf -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.suffix.lower() == ".pdf"), None)
    if src is None:
        raise SystemExit("no .pdf in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
