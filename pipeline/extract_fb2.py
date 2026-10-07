"""Extract an FB2 (or .fb2.zip) file into the same book.json model as extract_text.py.

ios/Sources/Import/FB2.swift is the same reading on the phone: every rule here is there too, and
tests/import_vectors holds what both must make of the same files, byte for byte."""

from __future__ import annotations

import base64
import binascii
import html
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_style  # noqa: E402
from extract_text import MAX_UNPACKED, block_sentences, build_offset_map, dump_book, safe_name  # noqa: E402

MARKS = ("em", "strong", "sup", "sub")
NOTE_TYPES = ("note", "comment")
MARK_TAGS = {"emphasis": "em", "i": "em", "strong": "strong", "b": "strong", "sup": "sup", "sub": "sub"}
# what may follow a note link for the space before it to go: `Кит {1}.` -> `Кит.`
CLOSING = set(".,;:!?…)]}»”’\"'")
DECLARED = re.compile(r"""encoding\s*=\s*["']([A-Za-z0-9._:-]+)["']""")
DECLARED_NAME = re.compile(r"""(encoding\s*=\s*["'])[A-Za-z0-9._:-]+(["'])""")
ENTITY = re.compile(r"&(?:(#[0-9]+;|#[xX][0-9a-fA-F]+;)|([A-Za-z][A-Za-z0-9]*);)?")
STAR_WINDOW = 3  # how many blocks back an asterisk footnote looks for its marker
HEADING_MAX = 80  # a top-level section without a title is named by a first line this short
DEPTH = 64  # an element this deep keeps only its text: no book nests further, and the walks stay shallow
# declared encoding names one decoder knows and the other does not (Apple's on the phone, Python's codecs here),
# read the same by both; the phone looks names up here first too
ENCODINGS = {
    "koi8r": "koi8-r", "koi8": "koi8-r", "koi8u": "koi8-u", "x-cp1251": "cp1251", "cp-1251": "cp1251",
    "windows1251": "cp1251", "cp-1252": "cp1252", "windows1252": "cp1252", "cp-866": "cp866",
    "x-mac-cyrillic": "mac-cyrillic", "x-mac-roman": "mac-roman",
    "1251": "cp1251", "1252": "cp1252", "cyrillic": "iso-8859-5", "latin": "latin-1",
}  # fmt: skip


def read_fb2(path: Path) -> bytes:
    if path.suffix == ".zip" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            info = next((i for i in z.infolist() if i.filename.lower().endswith(".fb2")), None)
            if info is None:
                raise SystemExit("В архиве нет книги")
            if info.file_size > MAX_UNPACKED:
                raise SystemExit(f"в архиве fb2 на {info.file_size / 1e6:.0f} МБ: это не книга")
            return z.read(info)
    return path.read_bytes()


def decode(data: bytes) -> str:
    """The file as text, in the encoding its XML declaration names (windows-1251, koi8-r...), else UTF-8. A byte
    that encoding does not define becomes U+FFFD: one bad byte costs a character, not the book. A BOM and
    blank lines before the declaration are dropped."""
    data = data.lstrip(b" \t\r\n").removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    head = data[:200].decode("utf-8", "replace")
    if head.startswith("<?xml"):
        m = DECLARED.search(head.split(">", 1)[0])
        if m and m.group(1).lower() not in ("utf-8", "utf8"):
            try:
                return as_utf8(data.decode(ENCODINGS.get(m.group(1).lower(), m.group(1)), "replace"))
            except LookupError:
                pass
    return as_utf8(data.decode("utf-8", "replace"))


def as_utf8(text: str) -> str:
    """The text without a BOM or blank lines before it, its declaration saying UTF-8, as the text now is."""
    text = text.lstrip("\ufeff \t\r\n")
    if not text.startswith("<?xml"):
        return text
    head = text.split(">", 1)[0]
    return DECLARED_NAME.sub(r"\1utf-8\2", head) + text[len(head) :]


def xml_char(v: int) -> bool:
    """A character XML allows (`&#1;` is not one: expat stops at it)."""
    return v in (9, 10, 13) or 0x20 <= v <= 0xD7FF or 0xE000 <= v <= 0xFFFD or 0x10000 <= v <= 0x10FFFF


def repair(text: str) -> str:
    """XML out of what FB2 files hold: an HTML entity (`&nbsp;`, `&hearts;`) becomes its character, a stray `&`
    and an unknown entity stay as text, a reference to a character XML does not allow (`&#1;`) goes."""

    def fix(m: re.Match) -> str:
        num, name = m.group(1), m.group(2)
        if num:
            digits = (num[2:-1] if num[1] in "xX" else num[1:-1]).lstrip("0") or "0"
            v = int(digits, 16 if num[1] in "xX" else 10) if len(digits) <= 8 else -1
            return m.group(0) if xml_char(v) else ""
        if name in ("amp", "lt", "gt", "quot", "apos"):
            return m.group(0)
        if name is None:
            return "&amp;"
        s = html.unescape(m.group(0))
        return f"&amp;{name};" if s == m.group(0) else "".join(f"&#{ord(c)};" for c in s)

    return ENTITY.sub(fix, text)


def parse(data: bytes) -> ET.Element:
    """The document as a tree of local names; attributes keep their prefix (`l:href`). Any FB2 namespace, none,
    or a prefix nobody declared reads the same."""
    tb = ET.TreeBuilder()
    p = expat.ParserCreate()
    p.buffer_text = True
    p.StartElementHandler = lambda name, attrs: tb.start(name.split(":", 1)[-1], attrs)
    p.EndElementHandler = lambda name: tb.end(name.split(":", 1)[-1])
    p.CharacterDataHandler = tb.data
    p.Parse(repair(decode(data)), True)
    return flatten(tb.close())


def flatten(root: ET.Element) -> ET.Element:
    """An element `DEPTH` levels down keeps only its text, as one paragraph (a section's too), so nothing after has
    to walk deeper (3000 nested `<emphasis>` would). Both walks here go without recursion."""
    stack = [(root, 0)]
    while stack:
        el, d = stack.pop()
        if d < DEPTH:
            stack.extend((c, d + 1) for c in el)
        elif len(el):
            out: list[str] = []
            todo: list = [el]  # elements to open, tails to write
            while todo:
                x = todo.pop()
                if isinstance(x, str):
                    out.append(x)
                    continue
                out.append(x.text or "")
                for c in reversed(x):
                    todo += [c.tail or "", c]
            del el[:]
            el.text = None
            ET.SubElement(el, "p").text = "".join(out)
    return root


def href(el) -> str:
    """`l:href`, `xlink:href` or any prefix, else a plain `href`."""
    v = next((v for k, v in el.attrib.items() if k.endswith(":href")), None)
    return (el.get("href") if v is None else v) or ""


def image_src(ref: str, cover_ids: set[str]) -> str:
    """The images/ path of a binary, named as the binaries are written."""
    return "images/" + (("cover" + Path(safe_name(ref)).suffix.lower()) if ref in cover_ids else safe_name(ref))


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def marker(link) -> str:
    """A note link's text as the reader shows it: `{721}` -> `721`, `[1]` -> `1`, `*` -> `*`."""
    return collapse("".join(link.itertext())).strip("[]{}()").strip()


def has_text(el) -> bool:
    return bool((el.text or "").strip() or any((c.tail or "").strip() for c in el))


def join_title(parts: list[str]) -> str:
    """Title paragraphs as one chapter title: `. ` between them unless the previous one ends a phrase."""
    out = ""
    for s in parts:
        if s:
            out = s if not out else out + (" " if out[-1] in ".,!?:;…—-" else ". ") + s
    return out


def remap(r: dict, f_start, f_end=None) -> None:
    """Move every offset of an inline result `r` (marks, notes, pictures) through `f_start` (`f_end` for ends)."""
    f_end = f_end or f_start
    for k in MARKS:
        out: list[list[int]] = []
        for a, b in r[k]:
            a, b = f_start(a), f_end(b)
            if b <= a:
                continue
            if out and out[-1][1] == a:
                out[-1][1] = b
            else:
                out.append([a, b])
        r[k] = out
    for n in r["notes"]:
        n["pos"] = f_end(n["pos"])
    for x in r["pics"]:
        x["pos"] = f_end(x["pos"])


def closes(text: str, p: int) -> bool:
    """What follows a removed note link at `p` lets it take the space before it: punctuation or the block's end.
    A straight quote right after a removed note link is a closing quote (space removed) only when the character
    after the quote is whitespace/punctuation/end; otherwise it opens a quotation and the space before the link
    stays."""
    nxt = text[p] if p < len(text) else ""
    if nxt in ('"', "'"):
        after = text[p + 1] if p + 1 < len(text) else ""
        return after == "" or after.isspace() or unicodedata.category(after).startswith("P")
    return nxt == "" or nxt in CLOSING


def unspace_notes(r: dict) -> None:
    """A removed note link takes the space before it when punctuation or the block's end follows
    (`Кит {1}.` -> `Кит.`) and leaves one between two words it glued (`организме[69]не` -> `организме не`).
    Where the space stays (`Слово {1} следующее`), the anchor sits right after the word before it."""
    for i in range(len(r["notes"]) - 1, -1, -1):
        text = r["text"]
        p = r["notes"][i]["pos"]
        nxt = text[p] if p < len(text) else ""
        if closes(text, p):
            k = p
            while k > 0 and text[k - 1].isspace():
                k -= 1
            if k < p:
                r["text"] = text[:k] + text[p:]
                remap(r, lambda x, k=k, p=p: x - (p - k) if x >= p else min(x, k))
        elif p > 0 and text[p - 1].isalnum() and nxt.isalnum():
            r["text"] = text[:p] + " " + text[p:]
            remap(r, lambda x, p=p: x + 1 if x >= p else x, lambda x, p=p: x + 1 if x > p else x)
        else:
            k = p
            while k > 0 and text[k - 1].isspace():
                k -= 1
            r["notes"][i]["pos"] = k


def note_value(r: dict, kinds: list | None = None):
    """A book note: a bare string when it is one paragraph of plain text, else an object."""
    v = {"text": r["text"], **{k: r[k] for k in MARKS if r[k]}}
    if r["pics"]:
        v["pics"] = r["pics"]
    if kinds:
        v["kinds"] = kinds
    return r["text"] if len(v) == 1 and "\n" not in r["text"] else v


class Fb2:
    def __init__(self, root: ET.Element) -> None:
        self.cover_ids = {
            href(im).lstrip("#") for cp in root.iter("coverpage") for im in cp if im.tag == "image"
        }  # fmt: skip
        bodies = [b for b in root if b.tag == "body"]
        # what the main text's note links (typed note/comment, or untyped) point to
        links = bodies[0].iter("a") if bodies else ()
        targets = {href(a).lstrip("#") for a in links if a.get("type") in (None, "", *NOTE_TYPES)}

        def holds_notes(b) -> bool:
            """A later body named notes/comments/footnotes, or one at least half of whose sections with an id the main
            text links to as notes. Any other (an appendix) is read as text."""
            if (b.get("name") or "").lower() in ("notes", "comments", "footnotes"):
                return True
            ids = [s.get("id") for s in b.iter("section") if s.get("id")]
            return bool(ids) and 2 * sum(x in targets for x in ids) >= len(ids)

        self.notes_bodies = [b for b in bodies[1:] if holds_notes(b)]
        self.main_bodies = [b for b in bodies if b not in self.notes_bodies]
        self.note_ids = {s.get("id") for b in self.notes_bodies for s in b.iter("section") if s.get("id")}
        self.written: set[str] = set()  # the images/ files the binaries became

    def src(self, ref: str) -> str | None:
        """A picture's images/ path, None when no file was written for it (no binary, broken base64)."""
        s = image_src(ref, self.cover_ids)
        return s if s.removeprefix("images/") in self.written else None

    def is_note_link(self, node) -> bool:
        """A link typed note/comment, or untyped, to a note there is: one to a missing note keeps its text."""
        if node.tag != "a":
            return False
        t = node.get("type")
        return (not t or t in NOTE_TYPES) and href(node).lstrip("#") in self.note_ids

    def title_text(self, el) -> str:
        """`itertext` without the text of note links: a title keeps no "{153}"."""
        out = [el.text or ""]
        for c in el:
            if not self.is_note_link(c):
                out.append(self.title_text(c))
            out.append(c.tail or "")
        return "".join(out)

    def inline(self, el, keep_lead: bool = False, links: bool = True) -> dict:
        """Text, mark ranges, note anchors and inline pictures: a picture, like a note, is only a place in the
        text. `links=False` (inside a note) keeps a note link's text as text."""
        parts: list[str] = []
        r: dict = {k: [] for k in MARKS} | {"notes": [], "pics": []}
        pos = 0

        def add(s: str | None, on: frozenset):
            nonlocal pos
            if not s:
                return
            for k in on:
                rs = r[k]
                if rs and rs[-1][1] == pos:
                    rs[-1][1] = pos + len(s)
                else:
                    rs.append([pos, pos + len(s)])
            parts.append(s)
            pos += len(s)

        def walk(node, on: frozenset):
            if links and self.is_note_link(node):
                n = {"pos": pos, "id": href(node).lstrip("#")}
                m = marker(node)
                if m:
                    n["m"] = m
                r["notes"].append(n)
            elif node.tag == "image":
                src = self.src(href(node).lstrip("#"))
                if src:
                    r["pics"].append({"pos": pos, "src": src})
            else:
                inner = on | {MARK_TAGS[node.tag]} if node.tag in MARK_TAGS else on
                add(node.text, inner)
                for c in node:
                    walk(c, inner)
            add(node.tail, on)

        add(el.text, frozenset())
        for c in el:
            walk(c, frozenset())
        r["text"] = "".join(parts)
        if links:
            unspace_notes(r)
        text = r["text"].replace("\xa0", " ")
        if keep_lead:  # a verse line keeps its indent
            lead = len(text) - len(text.lstrip(" "))
        else:
            cut = len(text) - len(text.lstrip())
            if cut:
                text = text[cut:]
                remap(r, lambda x: max(0, x - cut))
            lead = 0
        new = " " * lead + collapse(text[lead:])
        if new != text:
            m = build_offset_map(text, new)
            remap(r, lambda x: m[x])
        r["text"] = new
        return r


def extract(path: Path) -> dict:
    root = parse(read_fb2(path))
    fb = Fb2(root)
    desc = root.find("description/title-info")
    title = desc.findtext("book-title", default=path.stem) if desc is not None else path.stem
    author = ""
    if desc is not None and desc.find("author") is not None:
        a = desc.find("author")
        author = " ".join(x for x in (a.findtext("first-name", ""), a.findtext("last-name", "")) if x)
    blocks: list[dict] = []
    chapters: list[dict] = []
    notes: dict = {}
    stanza_n = 0
    gap = 0  # <empty-line/>s since the last block
    pending: list[dict] = []  # block images waiting for the next block of their chapter
    waiting: list[dict] = []  # note anchors of a paragraph that held nothing else, before any block
    star_k = 1  # the first starN id that may be free
    # a block keeps the id the file gives it, once; any other gets one that no element of the text has
    source_ids = {e.get("id") for b in fb.main_bodies for e in b.iter() if e.get("id")}
    used_ids: set[str] = set()
    img_dir = path.parent / "images"
    for binary in root:
        if binary.tag != "binary":
            continue
        bid = binary.get("id") or ""
        name = ("cover" + Path(safe_name(bid)).suffix.lower()) if bid in fb.cover_ids else safe_name(bid)
        if name and binary.text:  # the id comes from the file: it names a picture inside images/, nothing else
            try:
                data = base64.b64decode(binary.text)
            except (binascii.Error, ValueError):
                continue  # a broken picture is left out, the book is not
            img_dir.mkdir(exist_ok=True)
            (img_dir / name).write_bytes(data)
            fb.written.add(name)

    # ---- notes: every section with an id in a notes body, with its poems, pictures and marks
    def note_body(sec):
        paras: list[tuple[dict, str, int | None, list[str]]] = []
        imgs: list[str] = []
        stanza = [0]

        def para(el, kind, st=None):
            r = fb.inline(el, keep_lead=kind == "verse", links=False)
            if not r["text"].strip():
                imgs.extend(x["src"] for x in r["pics"])
                return
            paras.append((r, kind, st, imgs.copy()))
            imgs.clear()

        def walk(el, kind):
            for c in el:
                t = c.tag
                if t == "p":
                    para(c, kind)
                elif t == "v":
                    para(c, "verse", stanza[0])
                elif t in ("subtitle", "title"):
                    if el is sec and t == "title":
                        continue  # the note's number
                    if t == "title":
                        walk(c, "subtitle")
                    else:
                        para(c, "subtitle")
                elif t in ("text-author", "date"):
                    para(c, "text-author")
                elif t == "stanza":
                    stanza[0] += 1
                    walk(c, kind)
                elif t in ("poem", "cite", "epigraph"):
                    walk(c, "p" if t == "poem" else "cite")
                elif t == "image":
                    ref = href(c).lstrip("#")
                    src = fb.src(ref)
                    if src and ref not in fb.cover_ids:
                        imgs.append(src)
                elif t == "table":
                    for tr in c:
                        for cell in tr:
                            if cell.tag in ("td", "th"):
                                para(cell, kind)
                elif t == "section":
                    if not c.get("id"):
                        walk(c, kind)
                elif t != "empty-line":
                    if has_text(c):
                        para(c, kind)
                    else:
                        walk(c, kind)

        walk(sec, "p")
        out: dict = {"text": "", **{k: [] for k in MARKS}, "notes": [], "pics": []}
        kinds: list[list] = []
        prev = None
        for r, kind, st, before in paras:
            same = prev == ("verse", st) and kind == "verse"
            if prev is not None:
                out["text"] += "\n" if same else "\n\n"
            a = len(out["text"])
            out["pics"].extend({"pos": a, "src": s} for s in before)
            out["text"] += r["text"]
            for k in MARKS:
                out[k].extend([x + a, y + a] for x, y in r[k])
            out["pics"].extend({"pos": x["pos"] + a, "src": x["src"]} for x in r["pics"])
            b = len(out["text"])
            if same:
                kinds[-1][1] = b
            elif kind != "p":
                kinds.append([a, b, kind])
            prev = (kind, st)
        out["pics"].extend({"pos": len(out["text"]), "src": s} for s in imgs)
        return note_value(out, kinds)

    for body in fb.notes_bodies:
        for sec in body.iter("section"):
            nid = sec.get("id")
            if nid:
                notes[nid] = note_body(sec)

    # ---- the main text
    def star_note(r: dict, el) -> bool:
        """A paragraph `* text` right after a block with `word*` is that word's footnote: it leaves the text
        and becomes a note at the asterisk. `* * *` is a scene break, not a note, and `5*3` no marker."""
        nonlocal star_k
        text = r["text"]
        n = len(text) - len(text.lstrip("*"))
        if not 1 <= n <= 3 or n >= len(text) or not text[n].isspace() or not text.replace("*", "").strip():
            return False
        stars = "*" * n
        for j in range(len(blocks) - 1, max(-1, len(blocks) - 1 - STAR_WINDOW), -1):
            blk = blocks[j]
            if blk["chapter"] != len(chapters) - 1:
                break
            if blk["kind"] == "table":
                continue
            t = blk["text"]
            i = 0
            while i < len(t):
                if t[i] != "*":
                    i += 1
                    continue
                e = i
                while e < len(t) and t[e] == "*":
                    e += 1
                glued = i > 0 and t[i - 1].isalnum()
                if e - i == n and glued and (e == len(t) or t[e].isspace() or t[e] in CLOSING):
                    break
                i = e
            else:
                continue
            while f"star{star_k}" in notes:
                star_k += 1
            nid = f"star{star_k}"
            # the note's own links are no notes (one level): their text stays in it
            src = fb.inline(el, links=False)
            st = src["text"]
            b = len(st) - len(st[len(st) - len(st.lstrip("*")) :].lstrip())
            body = {"text": st[b:], "notes": []}
            for mk in MARKS:
                body[mk] = [[max(0, x - b), y - b] for x, y in src[mk] if y > b]
            body["pics"] = [{"pos": max(0, x["pos"] - b), "src": x["src"]} for x in src["pics"]]
            notes[nid] = note_value(body)
            br = {mk: blk.get(mk, []) for mk in MARKS} | {"notes": blk["notes"], "pics": blk.get("pics", [])}
            br["text"] = t[:i] + t[i + n :]
            remap(br, lambda x, i=i: x - n if x >= i + n else min(x, i))
            at = sum(1 for x in br["notes"] if x["pos"] <= i)
            br["notes"].insert(at, {"pos": i, "id": nid, "m": stars})
            blk["text"] = br["text"]
            blk["em"] = br["em"]
            for mk in MARKS[1:]:
                if br[mk]:
                    blk[mk] = br[mk]
                else:
                    blk.pop(mk, None)
            blk["notes"] = br["notes"]
            blk["sentences"] = block_sentences(blk["text"], blk["kind"])
            return True
        return False

    def block_id(el) -> str:
        own = el.get("id")
        base = own or f"b{len(blocks)}"
        bid, k = base, 1
        while bid in used_ids or (bid != own and bid in source_ids):
            k += 1
            bid = f"{base}-{k}"
        used_ids.add(bid)
        return bid

    def new_block(el, kind: str, text: str, r: dict, stanza=None, audio=True, rows=None) -> None:
        """A block of the chapter opened last: text after a nested chapter is that one's, as the reader shows it.
        Text before any chapter opens an untitled one."""
        nonlocal gap
        if not chapters:
            chapters.append({"id": "s0", "title": "", "level": 1, "first_block": len(blocks)})
        blk = {"images": pending.copy(), "id": block_id(el), "kind": kind, "chapter": len(chapters) - 1}
        blk |= {"stanza": stanza, "text": text, "em": r["em"]}
        blk |= {k: r[k] for k in MARKS[1:] if r[k]}
        blk |= {"notes": waiting + r["notes"]}
        waiting.clear()
        blk["sentences"] = block_sentences(text, kind) if rows is None else rows_sentences(text, rows)
        blk["audio"] = audio
        if r["pics"]:
            blk["pics"] = r["pics"]
        if rows is not None:
            blk["rows"] = rows
        st = extract_style.block_style(kind, {}, {}, gap)
        if st:
            blk["st"] = st
        blocks.append(blk)
        gap = 0
        pending.clear()

    def rows_sentences(text: str, rows: list) -> list[list[int]]:
        out = []
        for row in rows:
            a, b = row[0][0], row[-1][1]
            while a < b and text[a].isspace():
                a += 1
            while b > a and text[b - 1].isspace():
                b -= 1
            if b > a:
                out.append([a, b])
        return out

    def keep_notes(ns: list[dict]) -> None:
        """Anchors of a paragraph that held nothing else go to the end of the previous block, else to the
        start of the next."""
        if blocks:
            end = len(blocks[-1]["text"])
            blocks[-1]["notes"].extend({**n, "pos": end} for n in ns)
        else:
            waiting.extend({**n, "pos": 0} for n in ns)

    def add_block(el, kind: str, stanza=None) -> None:
        r = fb.inline(el, keep_lead=kind == "verse")
        if not r["text"].strip():
            pending.extend({"src": x["src"]} for x in r["pics"])  # a picture alone is set apart, as <image> is
            keep_notes(r["notes"])
            return
        if kind == "p" and r["text"].startswith("*") and star_note(r, el):
            return
        new_block(el, kind, r["text"], r, stanza)

    def add_table(el) -> None:
        """A table as one block: cells joined by tabs, rows by line breaks, each cell's range in `rows`."""
        out: dict = {k: [] for k in MARKS} | {"notes": [], "pics": []}
        text = ""
        rows: list[list[list[int]]] = []
        for tr in el:
            cells = [c for c in tr if c.tag in ("td", "th")] if tr.tag == "tr" else []
            if not cells:
                continue
            if rows:
                text += "\n"
            row = []
            for j, cell in enumerate(cells):
                if j:
                    text += "\t"
                r = fb.inline(cell)
                a = len(text)
                text += r["text"]
                row.append([a, len(text), 1] if cell.tag == "th" else [a, len(text)])
                for k in MARKS:
                    out[k].extend([x + a, y + a] for x, y in r[k])
                out["notes"].extend({**x, "pos": x["pos"] + a} for x in r["notes"])
                out["pics"].extend({"pos": x["pos"] + a, "src": x["src"]} for x in r["pics"])
            rows.append(row)
        if not text.strip():
            pending.extend({"src": x["src"]} for x in out["pics"])
            keep_notes(out["notes"])
            return
        new_block(el, "table", text, out, audio=False, rows=rows)

    def flush_images() -> None:
        """Images left at a chapter's end trail its last block."""
        if pending and blocks:
            blocks[-1]["images"].extend({**im, "after": True} for im in pending)
            pending.clear()

    def handle(el, ch: int, kind: str | None = None, stanza=None) -> None:
        """One element of chapter `ch` (the parent of a section in it)."""
        nonlocal stanza_n, gap
        t = el.tag
        if t == "section":
            section(el, ch)
        elif t == "title":  # a poem's, a stanza's: only a section's title is a chapter's
            for p in el:
                add_block(p, "subtitle")
        elif t == "subtitle":
            add_block(el, "subtitle")
        elif t == "p":
            add_block(el, kind or "p", stanza=stanza)
        elif t == "v":
            add_block(el, "verse", stanza=stanza)
        elif t == "poem":
            for c in el:
                handle(c, ch)
        elif t == "stanza":
            stanza_n += 1
            n = stanza_n  # lines after a nested stanza are still this one's
            for c in el:
                handle(c, ch, stanza=n)
        elif t in ("epigraph", "cite"):
            for c in el:
                handle(c, ch, kind=t)
        elif t == "text-author":
            add_block(el, "author")
        elif t == "date":
            add_block(el, "date")
        elif t == "image":
            ref = href(el).lstrip("#")
            src = fb.src(ref)
            if src and ref not in fb.cover_ids:
                pending.append({"src": src})
        elif t == "empty-line":
            gap += 1
        elif t == "table":
            add_table(el)
        elif has_text(el):
            add_block(el, kind or "p", stanza=stanza)
        else:
            for c in el:
                handle(c, ch, kind, stanza)

    def heading_line(sec) -> str:
        """The first line of a section without a title, when it reads as a heading."""
        for c in sec:
            if c.tag in ("subtitle", "p"):
                s = collapse(fb.title_text(c))
                if c.tag == "subtitle" or (len(s) <= HEADING_MAX and s[-1:] not in ".,;:"):
                    return s
                return ""
        return ""

    def section(sec, parent: int, top: bool = False) -> None:
        nonlocal gap
        ttl = next((c for c in sec if c.tag == "title"), None)
        title = join_title([collapse(fb.title_text(p)) for p in ttl]) if ttl is not None else ""
        if not title and not top:  # no chapter: its blocks are the enclosing chapter's
            for c in sec:
                handle(c, parent)
            return
        gap = 0  # a blank line before a chapter is not the chapter's
        flush_images()
        level = 1 if top or parent >= len(chapters) else chapters[parent]["level"] + 1
        ch = len(chapters)
        chapters.append(
            {"id": sec.get("id") or f"s{ch}", "title": title or heading_line(sec), "level": level,
             "first_block": len(blocks)}
        )  # fmt: skip
        for c in sec:
            if c is ttl:
                for p in c:
                    add_block(p, "title")
            else:
                handle(c, ch)

    for body in fb.main_bodies:
        bt = next((c for c in body if c.tag == "title"), None)
        if bt is not None:
            flush_images()
            heading = join_title([collapse(fb.title_text(p)) for p in bt])
            chapters.append({"id": "body", "title": heading, "level": 1, "first_block": len(blocks)})
            for p in bt:
                add_block(p, "title")
        for c in body:
            if c.tag == "section":
                section(c, len(chapters) - 1, top=True)
            elif c is not bt:
                handle(c, len(chapters) - 1 if chapters else 0)
    flush_images()
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
    (args.book_dir / "book.json").write_text(dump_book(book), encoding="utf-8")
    print(
        f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} "
        f"words={sum(len(b['text'].split()) for b in book['blocks'])} notes={len(book['notes'])}"
    )


if __name__ == "__main__":
    main()
