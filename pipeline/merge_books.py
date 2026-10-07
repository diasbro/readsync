"""Merge several extracted parts (book.json files) into one book: each part becomes a level-1
section, block and chapter indices are renumbered, notes are prefixed per part. A part that opens with
its own title block keeps it as the only narrated heading: its level-1 entry is for the contents alone."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import dump_book  # noqa: E402


def merge(parts: list[tuple[str, dict]], title: str = "", author: str = "") -> dict:
    if len(parts) == 1:
        book = parts[0][1]
        if title:
            book["title"] = title
        if author:
            book["author"] = author
        return book
    chapters: list[dict] = []
    blocks: list[dict] = []
    notes: dict[str, str] = {}
    for i, (part_title, book) in enumerate(parts, 1):
        prefix = f"p{i}_"
        base_ch = len(chapters)
        name = part_title or book.get("title") or f"Часть {i}"
        chapters.append({"id": f"part{i}", "title": name, "level": 1, "first_block": len(blocks)})
        own_title = bool(book["blocks"]) and book["blocks"][0].get("kind") == "title"
        if not own_title:  # a heading to hear where the part begins, unless the part brings its own
            blocks.append(
                {
                    "images": [],
                    "id": f"part{i}-title",
                    "kind": "title",
                    "chapter": base_ch,
                    "stanza": None,
                    "text": name,
                    "em": [],
                    "notes": [],
                    "sentences": [[0, len(name)]],
                    "audio": True,
                }
            )
        offset = len(chapters)
        for ch in book["chapters"]:
            c = dict(ch)
            c["level"] = min(3, ch["level"] + 1)
            c["first_block"] = ch["first_block"] + len(blocks)
            c["id"] = prefix + str(ch["id"])
            chapters.append(c)
        for b in book["blocks"]:
            nb = dict(b)
            nb["chapter"] = b["chapter"] + offset
            nb["id"] = prefix + str(b["id"])
            nb["notes"] = [{**n, "id": prefix + n["id"]} for n in b.get("notes", [])]  # the marker `m` too
            nb["images"] = [im if isinstance(im, dict) else {"src": im} for im in b.get("images", [])]
            blocks.append(nb)
        for k, v in book.get("notes", {}).items():
            notes[prefix + k] = v
    first = parts[0][1]
    return {
        "title": title or first.get("title", ""),
        "author": author or first.get("author", ""),
        "chapters": chapters,
        "blocks": blocks,
        "notes": notes,
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="merge parts/*/book.json into book.json")
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    args = ap.parse_args()
    part_dirs = sorted(p for p in (args.book_dir / "parts").iterdir() if (p / "book.json").exists())
    parts = []
    for p in part_dirs:
        book = json.loads((p / "book.json").read_text(encoding="utf-8"))
        parts.append(
            ((p / "title.txt").read_text(encoding="utf-8").strip() if (p / "title.txt").exists() else "", book)
        )
    merged = merge(parts, args.title, args.author)
    (args.book_dir / "book.json").write_text(dump_book(merged), encoding="utf-8")
    print(f"merged parts={len(parts)} chapters={len(merged['chapters'])} blocks={len(merged['blocks'])}")


if __name__ == "__main__":
    main()
