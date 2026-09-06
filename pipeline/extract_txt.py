"""Extract a plain-text book into the book.json model. Chapters are guessed from short
heading-like lines (Глава N, Часть N, roman numerals, all-caps lines)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import split_sentences  # noqa: E402

HEADING = re.compile(
    r"^(?:(?:глава|часть|книга|том|пролог|эпилог|предисловие|послесловие|chapter|part)\b.{0,60}|[IVXLC]{1,6}\.?|\d{1,3}\.?|(?-i:[А-ЯЁA-Z][А-ЯЁA-Z\s\d.,:;!?«»\"'()-]{2,60}))$",
    re.I,
)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251", "koi8-r", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def paragraphs(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    # books formatted with one line per paragraph vs. wrapped lines: if most lines are long, join wrapped lines
    long = sum(1 for ln in lines if len(ln) > 90)
    wrapped = long < len([ln for ln in lines if ln.strip()]) * 0.3
    out: list[str] = []
    buf: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        starts_new = ln.startswith((" ", "\t")) and buf
        if not wrapped or starts_new or HEADING.match(s) or (buf and HEADING.match(buf[-1])):
            if buf:
                out.append(" ".join(buf))
            buf = [s]
        else:
            buf.append(s)
    if buf:
        out.append(" ".join(buf))
    return [re.sub(r"\s+", " ", p).strip() for p in out if p.strip()]


def extract(path: Path) -> dict:
    paras = paragraphs(read_text(path))
    blocks: list[dict] = []
    chapters: list[dict] = [{"id": "s0", "title": "", "level": 1, "first_block": 0}]
    for p in paras:
        is_heading = (
            len(p) <= 80 and bool(HEADING.match(p.rstrip("."))) and not p.endswith((",", ";")) and len(p.split()) <= 8
        )
        if is_heading:
            chapters.append(
                {
                    "id": f"s{len(chapters)}",
                    "title": p,
                    "level": 1 if re.match(r"(?i)^(часть|книга|том)\b", p) else 2,
                    "first_block": len(blocks),
                }
            )
        blocks.append(
            {
                "images": [],
                "id": f"b{len(blocks)}",
                "kind": "title" if is_heading else "p",
                "chapter": len(chapters) - 1,
                "stanza": None,
                "text": p,
                "em": [],
                "notes": [],
                "sentences": [[0, len(p)]] if is_heading else split_sentences(p),
                "audio": True,
            }
        )
    if chapters and not chapters[0]["title"] and (len(chapters) == 1 or chapters[1]["first_block"] == 0):
        chapters = chapters[1:] or chapters
        for b in blocks:
            b["chapter"] = max(0, b["chapter"] - 1)
    return {"title": path.stem, "author": "", "chapters": chapters, "blocks": blocks, "notes": {}}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.txt -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.suffix.lower() == ".txt"), None)
    if src is None:
        raise SystemExit("no .txt in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
