"""Extract book text from the fantasy-worlds reader HTML into data/book.json.

Output model:
{
  "title": str, "author": str,
  "chapters": [{"id": str, "title": str, "level": int, "first_block": int}],
  "blocks": [{
      "id": "p68", "kind": "p|title|subtitle|verse|epigraph|author|cite",
      "chapter": int, "stanza": int|None,
      "text": str,                # plain text
      "em": [[start, end], ...],  # italic ranges (char offsets in text)
      "notes": [{"pos": int, "id": "n_1"}],
      "sentences": [[start, end], ...],
      "audio": bool               # expected to be narrated
  }],
  "notes": {"n_1": "text", ...}
}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

ROOT = Path(__file__).resolve().parent.parent

SENT_END = re.compile(r'[.!?…]+[»"”)\]]*')
ABBREV = {"т", "е", "д", "г", "гг", "стр", "см", "им", "ул", "св", "проф", "др", "пр", "тыс", "млн", "млрд"}


def inline_text(el: Tag) -> tuple[str, list[list[int]], list[dict]]:
    """Flatten inline content: plain text, italic ranges, note refs."""
    parts: list[str] = []
    em: list[list[int]] = []
    notes: list[dict] = []
    pos = 0

    def walk(node, in_em: bool):
        nonlocal pos
        if isinstance(node, NavigableString):
            s = str(node)
            if not s:
                return
            if in_em:
                if em and em[-1][1] == pos:
                    em[-1][1] = pos + len(s)
                else:
                    em.append([pos, pos + len(s)])
            parts.append(s)
            pos += len(s)
            return
        if not isinstance(node, Tag):
            return
        if node.name == "sup" and "note" in node.get("class", []):
            a = node.find("a")
            nid = a.get("data-note-id") if a else None
            if nid:
                notes.append({"pos": pos, "id": nid})
            return
        if node.name in ("script", "style", "svg"):
            return
        child_em = in_em or node.name in ("em", "i")
        if node.name in ("p", "div", "br") and parts and not parts[-1].endswith(" "):
            parts.append(" ")
            pos += 1
        for c in node.children:
            walk(c, child_em)

    for c in el.children:
        walk(c, False)
    text = "".join(parts).replace("\xa0", " ")
    # collapse whitespace but keep leading indentation for verse
    lead = len(text) - len(text.lstrip(" "))
    body = re.sub(r"\s+", " ", text[lead:]).strip()
    text2 = " " * lead + body
    # recompute em/notes offsets after collapse via mapping
    if text2 != text:
        mapping = build_offset_map(text, text2)
        em = [[mapping[a], mapping[b]] for a, b in em]
        notes = [{"pos": mapping[n["pos"]], "id": n["id"]} for n in notes]
        em = [[a, b] for a, b in em if b > a]
    return text2, em, notes


def build_offset_map(old: str, new: str) -> list[int]:
    """Map offsets in `old` to offsets in `new` where `new` is `old` with whitespace collapsed."""
    mapping = [0] * (len(old) + 1)
    j = 0
    lead = len(old) - len(old.lstrip(" "))
    for i, ch in enumerate(old):
        mapping[i] = min(j, len(new))
        if i < lead:
            j += 1
            continue
        if ch.isspace():
            if j < len(new) and new[j] == " " and (j == 0 or not new[j - 1].isspace()) and mapping[i] == j:
                # consume a single space in new for a run of whitespace in old
                j += 1
            continue
        # non-space: advance j to matching char
        while j < len(new) and new[j] != ch:
            j += 1
        j += 1
    mapping[len(old)] = len(new)
    # clamp
    return [min(m, len(new)) for m in mapping]


def split_sentences(text: str) -> list[list[int]]:
    """Return [start,end] char ranges of sentences in text (Russian-aware heuristic)."""
    n = len(text)
    out: list[list[int]] = []
    start = 0
    i = 0
    while i < n:
        m = SENT_END.match(text, i)
        if not m:
            i += 1
            continue
        end = m.end()
        # look ahead: sentence boundary if followed by whitespace and an uppercase/quote/dash, or end of text
        j = end
        while j < n and text[j] == " ":
            j += 1
        if end >= n:
            out.append([start, n])
            start = n
            break
        word_before = re.search(r"([А-Яа-яЁёA-Za-z]+)\.?$", text[start : m.start() + 1])
        is_abbrev = bool(word_before) and word_before.group(1).lower() in ABBREV and text[m.start()] == "."
        next_ch = text[j] if j < n else ""
        boundary = j > end and not is_abbrev and (next_ch.isupper() or next_ch in '«"“–—-([' or next_ch.isdigit())
        if boundary:
            out.append([start, end])
            start = j
            i = j
        else:
            i = end
    if start < n:
        out.append([start, n])
    # strip whitespace inside ranges
    cleaned = []
    for a, b in out:
        while a < b and text[a] == " ":
            a += 1
        while b > a and text[b - 1] == " ":
            b -= 1
        if b > a:
            cleaned.append([a, b])
    return cleaned


def extract_generic(soup: BeautifulSoup, src: Path) -> dict:
    """Any HTML page: take the container holding most paragraph text, keep headings and paragraphs."""
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        t.decompose()
    best, best_len = soup.body or soup, 0
    for cand in (soup.body or soup).find_all(["div", "article", "section", "main", "td"]):
        n = sum(len(p.get_text()) for p in cand.find_all("p", recursive=False))
        if n > best_len:
            best, best_len = cand, n
    blocks: list[dict] = []
    chapters: list[dict] = [{"id": "s0", "title": "", "level": 1, "first_block": 0}]

    def add(el: Tag, kind: str):
        text, em, nrefs = inline_text(el)
        if not text.strip():
            return
        blocks.append(
            {
                "images": [],
                "id": el.get("id") or f"b{len(blocks)}",
                "kind": kind,
                "chapter": len(chapters) - 1,
                "stanza": None,
                "text": text,
                "em": em,
                "notes": [],
                "sentences": split_sentences(text) if kind == "p" else [[0, len(text)]],
                "audio": True,
            }
        )

    for el in best.find_all(["h1", "h2", "h3", "h4", "p", "blockquote"]):
        if el.name in ("h1", "h2", "h3"):
            chapters.append(
                {
                    "id": f"s{len(chapters)}",
                    "title": el.get_text(" ", strip=True),
                    "level": 2,
                    "first_block": len(blocks),
                }
            )
            add(el, "title")
        elif el.name == "h4":
            add(el, "subtitle")
        elif el.name == "blockquote":
            for p in el.find_all("p"):
                add(p, "cite")
        elif el.find_parent("blockquote") is None:
            add(el, "p")
    if chapters[0]["title"] == "" and len(chapters) > 1 and chapters[1]["first_block"] == 0:
        chapters.pop(0)
        for b in blocks:
            b["chapter"] = max(0, b["chapter"] - 1)
    title = soup.title.get_text(strip=True) if soup.title else src.stem
    return {"title": title, "author": "", "chapters": chapters, "blocks": blocks, "notes": {}}


def extract(src: Path) -> dict:
    soup = BeautifulSoup(src.read_text(encoding="utf-8", errors="replace"), "html.parser")
    article = soup.find("article", id="book-content")
    if article is None:
        return extract_generic(soup, src)
    blocks: list[dict] = []
    chapters: list[dict] = []
    notes: dict[str, str] = {}
    stanza_counter = [0]
    pending_images: list[str] = []  # illustrations seen since the last block; attached to the next block

    def add_block(el: Tag, kind: str, chapter: int, audio: bool = True, stanza=None, bid=None):
        text, em, nrefs = inline_text(el)
        if not text.strip():
            return
        blocks.append(
            {
                "images": pending_images.copy(),
                "id": bid or el.get("id") or f"b{len(blocks)}",
                "kind": kind,
                "chapter": chapter,
                "stanza": stanza,
                "text": text,
                "em": em,
                "notes": nrefs,
                "sentences": [[0, len(text)]] if kind != "p" else split_sentences(text),
                "audio": audio,
            }
        )
        pending_images.clear()

    def walk_section(sec: Tag, level: int):
        h2 = sec.find("h2", recursive=False)
        title = (
            " ".join(p.get_text(" ", strip=True) for p in h2.find_all("p"))
            if h2 and h2.find("p")
            else (h2.get_text(" ", strip=True) if h2 else "")
        )
        title = re.sub(r"\s+", " ", title).strip()
        if title == "<title unassigned>":
            title = "* * *"
        ch_idx = len(chapters)
        chapters.append({"id": sec.get("id"), "title": title, "level": level, "first_block": len(blocks)})
        if h2 is not None:
            add_block(h2, "title", ch_idx, bid=f"t-{sec.get('id')}")
        for child in sec.children:
            if not isinstance(child, Tag):
                continue
            handle(child, ch_idx, level)

    def handle(el: Tag, ch_idx: int, level: int, kind_override: str | None = None, audio: bool = True, stanza=None):
        name = el.name
        cls = el.get("class", [])
        if name == "section":
            walk_section(el, level + 1)
        elif name == "h2":
            return  # handled by walk_section
        elif name == "h3":
            add_block(el, "subtitle", ch_idx, bid=f"h3-{len(blocks)}")
        elif name == "p":
            kind = kind_override or ("verse" if "verse" in cls else "author" if "text-author" in cls else "p")
            add_block(el, kind, ch_idx, audio=audio, stanza=stanza)
        elif name == "div":
            if "poem" in cls:
                for st in el.find_all("div", class_="stanza", recursive=False):
                    stanza_counter[0] += 1
                    for p in st.find_all("p", recursive=False):
                        handle(p, ch_idx, level, kind_override="verse", stanza=stanza_counter[0])
                for p in el.find_all("p", class_="text-author", recursive=False):
                    handle(p, ch_idx, level, kind_override="author")
            elif "epigraph" in cls:
                for p in el.find_all("p", recursive=False):
                    handle(
                        p, ch_idx, level, kind_override="author" if "text-author" in p.get("class", []) else "epigraph"
                    )
            elif el.get("id") == "annotation":
                for p in el.find_all("p", recursive=False):
                    handle(p, ch_idx, level, kind_override="annotation", audio=False)
            elif "img-wrap" in cls:
                img = el.find("img")
                src = (img.get("data-src") or img.get("src") or "") if img else ""
                if src and not src.startswith("cover"):
                    entry = {"src": "images/" + src.rsplit("/", 1)[-1]}
                    if img.get("width") and img.get("height"):
                        entry["w"], entry["h"] = int(img["width"]), int(img["height"])
                    pending_images.append(entry)
                return
            elif "empty-line" in cls:
                return
            else:
                for c in el.children:
                    if isinstance(c, Tag):
                        handle(c, ch_idx, level, kind_override, audio, stanza)
        elif name == "cite":
            for p in el.find_all("p", recursive=False):
                handle(p, ch_idx, level, kind_override="cite")
        elif name in ("script", "img", "svg"):
            return
        else:
            for c in el.children:
                if isinstance(c, Tag):
                    handle(c, ch_idx, level, kind_override, audio, stanza)

    for sec in article.find_all("section", recursive=False):
        cls = sec.get("class", [])
        if "notes" in cls:
            for note in sec.find_all("div", class_="note"):
                nid = note.get("id", "").replace("note-", "")
                body = note.find("div", class_="note__body")
                notes[nid] = re.sub(r"\s+", " ", body.get_text(" ", strip=True)) if body else ""
            continue
        walk_section(sec, 1)

    meta = soup.find("meta", attrs={"name": "author"})
    return {
        "title": (soup.title.get_text(strip=True) if soup.title else src.stem),
        "author": meta.get("content", "") if meta else "",
        "chapters": chapters,
        "blocks": blocks,
        "notes": notes,
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.html (fantasy-worlds reader page) -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    book = extract(args.book_dir / "book.html")
    (args.book_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    n_audio = sum(1 for b in book["blocks"] if b["audio"])
    n_sent = sum(len(b["sentences"]) for b in book["blocks"])
    n_words = sum(len(b["text"].split()) for b in book["blocks"] if b["audio"])
    print(
        f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} audio_blocks={n_audio} "
        f"sentences={n_sent} words={n_words} notes={len(book['notes'])}"
    )


if __name__ == "__main__":
    main()
