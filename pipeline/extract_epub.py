"""Extract an EPUB into the book.json model (same shape as extract_text.py).

Chapters come from the book's table of contents (EPUB3 nav, else the EPUB2 NCX), or from h1-h4 when it has
none. Every text-bearing block is read: paragraphs, text directly in a div/li/td/dd/pre/figcaption, list items
with their number or bullet, block quotes, tables (kind "table"), verse and epigraphs by class, epub:type or
shape. A link to a short note-like element (noteref, aside, footnote, endnote list item, class note/footnote/fn)
makes that element a note and takes it out of the reading flow. Pictures inside text are `pics`, a picture on
its own goes before the next block, or after the last one at a chapter's end.
"""

from __future__ import annotations

import html
import posixpath
import re
import sys
import unicodedata
import warnings
import zipfile
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, CData, NavigableString, Tag, XMLParsedAsHTMLWarning

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_style  # noqa: E402
from extract_text import BLOCK_TAGS, MAX_UNPACKED, block_sentences, dump_book, inline_text  # noqa: E402

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
SKIP = {"script", "style", "nav", "head", "title", "template"}
# site chrome of Wikisource exports and the like: never read
BOILERPLATE = {"ws-noexport", "noprint", "licensecontainer", "license", "mw-editsection", "navbox", "metadata",
               "ws-header", "headertemplate", "header_notes", "wst-auxtoc"}  # fmt: skip
LAYOUT = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "table", "blockquote", "pre"}
MAX_CELL = 100  # characters: a longer cell is a column of text, not data
INLINE = {"a", "span", "sup", "sub", "em", "strong", "i", "b", "small", "big", "u", "s", "abbr", "cite", "code", "q",
          "font", "mark", "del", "ins", "dfn", "kbd", "var", "samp", "tt", "label", "time"}  # fmt: skip
NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
NOTE_ROLES = {"doc-footnote", "doc-endnote", "doc-rearnote", "doc-note"}
NOTES_TYPES = {"footnotes", "endnotes", "rearnotes"}
NOTES_ROLES = {"doc-endnotes", "doc-footnotes"}
NOTES_CLASSES = {"notes", "footnotes", "endnotes", "rearnotes"}
NOTE_CLASS = re.compile(r"(foot|end|rear|side)?note([-_]?(text|body|item|\d+))?|fn\d*|fnote\d*|notetext")
VERSE_TYPES = {"z3998:poem", "z3998:verse", "z3998:song", "z3998:hymn"}
MAX_NOTE = 10000  # characters: a longer target is a part of the text, not a note
TITLE_END = ".!?:;…—-,"
LIST_MARK = re.compile(r"\d+[.)]\s|[•·▪◦‣*]\s")  # the item's own number or bullet; "A. ", "I. ", "— " are text
INT = re.compile(r"\s*(-?[0-9]{1,100})\s*")
MAX_DEPTH = 100  # elements: deeper nesting is flattened into the element at this depth
TOO_BIG = "epub распаковывается больше чем в 500 МБ: это не книга"
# charsets a document may declare that both importers read alike (TXT's code page tables on the phone)
CHARSETS = {"utf-8": "utf-8", "utf8": "utf-8", "windows-1251": "cp1251", "cp1251": "cp1251", "x-cp1251": "cp1251",
            "koi8-r": "koi8_r", "koi8r": "koi8_r", "ibm866": "cp866", "cp866": "cp866",
            "x-mac-cyrillic": "mac_cyrillic", "iso-8859-1": "latin-1", "latin1": "latin-1",
            "latin-1": "latin-1"}  # fmt: skip
DECLARED = re.compile(r"""<\?xml[^>]*?[ \t\r\n]encoding[ \t\r\n]*=[ \t\r\n]*["']([A-Za-z0-9._:-]+)["']""")
META_CHARSET = re.compile(r"""<meta[^>]*?charset[ \t\r\n]*=[ \t\r\n]*["']?([A-Za-z0-9._:-]+)""", re.I)


def tokens(el: Tag, attr: str) -> set[str]:
    v = el.get(attr) or ""
    return set((" ".join(v) if isinstance(v, list) else v).lower().split())


def classes(el: Tag) -> list[str]:
    return list(el.get("class") or [])


def is_text(node) -> bool:
    return type(node) is NavigableString or isinstance(node, CData)


def attrs_of(s: str) -> dict:
    """The attributes of one OPF tag, in either quote style, entities resolved; the first of a name counts."""
    out: dict = {}
    for m in re.finditer(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", s):
        out.setdefault(m.group(1), html.unescape(m.group(2) if m.group(2) is not None else m.group(3)))
    return out


def tags(xml: str, name: str) -> list[dict]:
    """Every `<name …>` of the OPF with any namespace prefix (`<item>`, `<opf:item>`), as attributes."""
    return [attrs_of(m.group(1)) for m in re.finditer(rf"<(?:[\w-]+:)?{name}\b([^>]*)>", xml)]


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<(?:[\w-]+:)?{name}\b[^>]*>(.*?)</(?:[\w-]+:)?{name}\s*>", xml, re.S)
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""


def href_path(href: str) -> tuple[str, str]:
    """(decoded path, decoded fragment) of a link."""
    path, _, frag = href.strip().partition("#")
    return unquote(path, errors="replace"), unquote(frag, errors="replace")


def decode(data: bytes) -> str:
    """A document's text: a byte order mark, else the charset its XML declaration or <meta> names, else UTF-8."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom) :].decode(enc, "replace")
    head = data[:1024].decode("latin-1")
    m = DECLARED.search(head) or META_CHARSET.search(head)
    return data.decode(CHARSETS.get(m.group(1).lower(), "utf-8") if m else "utf-8", "replace")


def parse(data: bytes) -> BeautifulSoup:
    """A document as tag soup, nesting deeper than MAX_DEPTH flattened: the element at that depth keeps, in order,
    the text and the empty elements (a picture, a line break) of everything below it, a block's set off by spaces."""
    with warnings.catch_warnings():  # XHTML and the NCX are XML: read the way the phone reads them, as tag soup
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(decode(data), "html.parser")
    stack: list[tuple[Tag, int]] = [(soup, 0)]
    while stack:
        el, d = stack.pop()
        for c in el.find_all(True, recursive=False):
            if d + 1 < MAX_DEPTH:
                stack.append((c, d + 1))
                continue
            for x in c.find_all(True):
                if x.contents:
                    if x.name in BLOCK_TAGS:  # a block's content stays apart from its neighbours'
                        x.insert(0, " ")
                        x.append(" ")
                    x.unwrap()
    return soup


def image_name(path: str) -> str:
    """A picture's file name: the zip name's last part in NFC, each run of characters other than letters, digits
    (Unicode categories L*, N*) and `._-` one `_`."""
    out: list[str] = []
    run = False
    for ch in unicodedata.normalize("NFC", posixpath.basename(path)):
        if ch in "._-" or unicodedata.category(ch)[0] in "LN":
            out.append(ch)
            run = False
        elif not run:
            out.append("_")
            run = True
    return "".join(out)


def join_title(lines: list[str]) -> str:
    out = ""
    for p in (re.sub(r"\s+", " ", x).strip() for x in lines):
        if p:
            out = p if not out else out + (" " if out[-1] in TITLE_END else ". ") + p
    return out


class Book:
    """The package: the zip, the manifest, the spine's documents (parsed), their ids and the table of contents."""

    def __init__(self, path: Path) -> None:
        self.z = zipfile.ZipFile(path)
        if sum(i.file_size for i in self.z.infolist()) > MAX_UNPACKED:
            raise SystemExit(TOO_BIG)
        # names compared in NFC, the last of a name counting (zipfile's own rule): an NFC href finds an NFD entry
        self.canon: dict[str, str] = {}
        self.lower: dict[str, str] = {}
        for n in self.z.namelist():
            self.canon[unicodedata.normalize("NFC", n)] = n
            self.lower[unicodedata.normalize("NFC", n).lower()] = n
        self.image_names: dict[str, str] = {}  # zip name -> file name in images/
        container = self.resolve("META-INF/container.xml")
        text = self.z.read(container).decode("utf-8", "replace") if container else ""
        m = re.search(r"""full-path\s*=\s*["']([^"']+)["']""", text)
        opf = self.resolve(html.unescape(m.group(1))) if m else None
        if opf is None:
            raise SystemExit("EPUB without OPF")
        base = posixpath.dirname(opf)
        xml = decode(self.z.read(opf))
        self.items: dict[str, dict] = {}
        order: list[dict] = []
        for it in tags(xml, "item"):
            if "id" in it and "href" in it:
                it["path"] = self.resolve(posixpath.join(base, href_path(it["href"])[0]))
                if it["id"] not in self.items:
                    order.append(it)
                self.items[it["id"]] = it
        nav = next((it for it in order if "nav" in it.get("properties", "").split()), None)
        skip = {nav["path"]} if nav else set()  # the table of contents is not read as text
        self.docs: list[str] = []
        for ref in tags(xml, "itemref"):
            it = self.items.get(ref.get("idref", ""))
            if it and self.is_doc(it) and it["path"] and it["path"] not in self.docs and it["path"] not in skip:
                self.docs.append(it["path"])
        if not self.docs:  # no usable spine: the manifest's documents in order
            for it in order:
                if self.is_doc(it) and it["path"] and it["path"] not in self.docs and it["path"] not in skip:
                    self.docs.append(it["path"])
        self.title, self.author = _tag(xml, "title"), _tag(xml, "creator")
        metas = tags(xml, "meta")
        cover = next((self.items.get(t.get("content", "")) for t in metas if t.get("name") == "cover"), None)
        if cover is None:
            cover = next((it for it in order if "cover-image" in it.get("properties", "").split()), None)
        self.cover = cover["path"] if cover else None
        self.soups = {d: parse(self.z.read(d)) for d in self.docs}
        for d in [d for d in self.docs if credits_page(self.soups[d])]:
            self.docs.remove(d)
            del self.soups[d]
        self.ids: dict[str, dict[str, Tag]] = {}
        self.order: dict[int, int] = {}
        self.parent_doc: dict[int, str] = {}
        k = 0
        for d in self.docs:
            ids: dict[str, Tag] = {}
            for el in self.soups[d].find_all(True):
                self.order[id(el)] = k
                k += 1
                for key in (el.get("id"), el.get("name") if el.name == "a" else None):
                    if key and key not in ids:
                        ids[key] = el
            self.ids[d] = ids
        spine = next(iter(tags(xml, "spine")), {})
        ncx = self.items.get(spine.get("toc", "")) or next(
            (it for it in order if it.get("media-type") == "application/x-dtbncx+xml"), None
        )
        self.toc = self.nav_toc(nav["path"]) if nav and nav["path"] else []
        if not self.toc and ncx and ncx["path"]:
            self.toc = self.ncx_toc(ncx["path"])
        # headings per level, those of a notes section left out; the top level that has two or more is the
        # chapters' (deeper ones are headings inside a chapter)
        heads: dict[int, int] = {}
        for d in self.docs:
            for h in self.soups[d].find_all(list(HEADINGS)):
                if not any(Notes.plural(x) for x in h.parents):
                    heads[HEADINGS[h.name]] = heads.get(HEADINGS[h.name], 0) + 1
        top = next((heads[k] for k in sorted(heads) if heads[k] >= 2), 0)
        few = len(self.toc) < 2 or len({e[0] for e in self.toc}) * 2 < len(self.docs)
        if heads and (few or len(self.toc) * 2 < top):
            self.toc = []  # a table of contents of a few files, not of the chapters: chapters from the headings

    @staticmethod
    def is_doc(it: dict) -> bool:
        return "html" in it.get("media-type", "") or it["href"].lower().endswith((".xhtml", ".html", ".htm"))

    def resolve(self, path: str) -> str | None:
        """A path inside the zip as it is spelled there: exact, else ignoring case."""
        p = unicodedata.normalize("NFC", posixpath.normpath(path).lstrip("/"))
        got = self.canon.get(p)
        return got if got is not None else self.lower.get(p.lower())

    def link(self, href: str, doc: str) -> tuple[str | None, str]:
        path, frag = href_path(href)
        return (self.resolve(posixpath.join(posixpath.dirname(doc), path)) if path else doc), frag

    def soup(self, path: str) -> BeautifulSoup:
        if path in self.soups:
            return self.soups[path]
        return parse(self.z.read(path))

    def entry(self, href: str, doc: str, title: str, level: int, out: list) -> None:
        path, frag = self.link(href, doc)
        if path in self.soups:
            out.append((path, frag, title, min(level, 4)))

    def nav_toc(self, path: str) -> list:
        out: list = []
        navs = self.soup(path).find_all("nav")
        nav = next((n for n in navs if "toc" in tokens(n, "epub:type")), navs[0] if navs else None)
        ol = nav.find("ol") if nav else None

        def walk(ol: Tag, level: int) -> None:
            for li in ol.find_all("li", recursive=False):
                label = next((c for c in li.children if isinstance(c, Tag) and c.name != "ol"), None)
                a = None if label is None else label if label.name == "a" else label.find("a")
                if a is not None and a.get("href"):
                    self.entry(a["href"], path, join_title([label.get_text()]), level, out)
                sub = li.find("ol", recursive=False)
                if sub is not None:
                    walk(sub, level + 1)

        if ol is not None:
            walk(ol, 1)
        return out

    def ncx_toc(self, path: str) -> list:
        out: list = []
        nav_map = self.soup(path).find("navmap")

        def walk(el: Tag, level: int) -> None:
            for p in el.find_all("navpoint", recursive=False):
                label, content = p.find("navlabel", recursive=False), p.find("content", recursive=False)
                if content is not None and content.get("src"):
                    self.entry(content["src"], path, join_title([label.get_text() if label else ""]), level, out)
                walk(p, level + 1)

        if nav_map is not None:
            walk(nav_map, 1)
        return out

    def image(self, el: Tag, doc: str, img_dir: Path) -> str | None:
        """Save a picture an <img> or svg <image> shows into images/ (once, under a name of its own); its src."""
        src = el.get("src") if el.name == "img" else el.get("xlink:href") or el.get("href")
        if not src:
            return None
        full = self.resolve(posixpath.join(posixpath.dirname(doc), href_path(src)[0]))
        if full is None:
            return None
        name = self.image_names.get(full)
        if name is None:
            name = image_name(full)
            root, ext = posixpath.splitext(name)
            taken = {n.lower() for n in self.image_names.values()}
            k = 1
            while name.lower() in taken:  # the same name in another folder
                k += 1
                name = f"{root}-{k}{ext}"
            img_dir.mkdir(exist_ok=True)
            (img_dir / name).write_bytes(self.z.read(full))
            self.image_names[full] = name
        return f"images/{name}"


class Notes:
    """Which links are note references and which elements are their notes (KOReader's and foliate's rules)."""

    def __init__(self, bk: Book) -> None:
        self.bk = bk
        self.links: dict[int, tuple[str, str]] = {}  # id(a) -> (note id, marker)
        self.containers: dict[int, Tag] = {}
        self.marks: dict[int, set[str]] = {}  # per note: the markers of the links to it
        self.list: list[tuple[str, Tag, str, list[Tag]]] = []  # (note id, container, its doc, links to it)
        found: list[tuple[Tag, Tag, str, str]] = []  # (link, container, doc of the container, fragment)
        for doc in bk.docs:
            for a in bk.soups[doc].find_all("a", href=True):
                got = self.judge(a, doc)
                if got:
                    found.append((a, *got))
        held = {id(c) for _, c, _, _ in found}
        found = [f for f in found if not any(id(p) in held for p in f[0].parents)]  # a note's links are no notes
        used: set[str] = set()
        by: dict[int, tuple] = {}
        for a, c, cdoc, frag in found:
            if id(c) not in by:
                nid = first = c.get("id") or frag
                k = 1
                while nid in used:
                    k += 1
                    nid = f"{first}-{k}"
                used.add(nid)
                by[id(c)] = (nid, c, cdoc, [])
                self.list.append(by[id(c)])
                self.containers[id(c)] = c
            by[id(c)][3].append(a)
            m = marker(a)
            self.links[id(a)] = (by[id(c)][0], m)
            self.marks.setdefault(id(c), set()).add(m)
        # per note: its references, the inline elements around them and their blocks, where a back link points
        self.near: dict[int, set[int]] = {}
        for a, c, _, _ in found:
            x = a
            while isinstance(x, Tag):
                self.near.setdefault(id(c), set()).add(id(x))
                if x.name not in INLINE:
                    break
                x = x.parent

    def judge(self, a: Tag, doc: str) -> tuple[Tag, str, str] | None:
        bk = self.bk
        types, roles = tokens(a, "epub:type"), tokens(a, "role")
        if types & {"backlink", "link"} or roles & {"doc-backlink", "doc-link"} or a.find_parent("nav"):
            return None
        tdoc, frag = bk.link(a["href"], doc)
        target = bk.ids.get(tdoc, {}).get(frag) if tdoc and frag else None
        if target is None:
            return None
        block = target
        while block.name in INLINE and isinstance(block.parent, Tag):
            block = block.parent
        if block.name in ("body", "html", "[document]"):
            return None
        note, x = None, block
        while isinstance(x, Tag) and x.name not in ("body", "html", "[document]"):
            if self.note_like(x):
                note = x
                break
            if x.name in ("section", "article", "main") or self.plural(x):
                break
            x = x.parent
        strong = note is not None or self.in_notes(block)
        c = note or block
        if c is a or any(p is c for p in a.parents) or any(p is a for p in c.parents):
            return None
        sup, p = False, a.parent
        for _ in range(3):  # the link inside a <sup>, or inside inline elements inside one
            if not isinstance(p, Tag) or p.name not in INLINE:
                break
            if p.name == "sup":
                sup = True
                break
            p = p.parent
        kids = [k for k in a.children if isinstance(k, Tag)]
        sup = sup or (
            len(kids) == 1 and kids[0].name == "sup" and not any(is_text(k) and k.strip() for k in a.children)
        )
        if not (strong or sup or "noteref" in types or "doc-noteref" in roles):
            return None
        text = c.get_text()
        if len(text) > MAX_NOTE or (not text.strip() and c.find(["img", "image"]) is None):
            return None
        if not strong:
            if c.name in ("h1", "h2", "h3", "h4", "h5", "h6") or c.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
                return None
            if bk.order.get(id(c), 0) < bk.order.get(id(a), 0):
                return None  # a link back to the text
        return c, tdoc, frag

    @staticmethod
    def plural(x: Tag) -> bool:
        return bool(tokens(x, "epub:type") & NOTES_TYPES or tokens(x, "role") & NOTES_ROLES) or any(
            c.lower() in NOTES_CLASSES for c in classes(x)
        )

    def in_notes(self, x: Tag) -> bool:
        return any(self.plural(p) for p in [x, *x.parents] if isinstance(p, Tag))

    def note_like(self, x: Tag) -> bool:
        if x.name == "aside" or tokens(x, "epub:type") & NOTE_TYPES or tokens(x, "role") & NOTE_ROLES:
            return True
        if any(NOTE_CLASS.fullmatch(c.lower()) for c in classes(x)):
            return True
        return x.name == "li" and isinstance(x.parent, Tag) and x.parent.name in ("ol", "ul") and self.in_notes(x)

    def backlink(self, a: Tag, doc: str, note: Tag) -> bool:
        """A link inside a note back to the text that refers to it (or typed as one)."""
        if tokens(a, "epub:type") & {"backlink"} or tokens(a, "role") & {"doc-backlink"}:
            return True
        m = marker(a)
        if m not in self.marks.get(id(note), ()) and any(ch.isalnum() for ch in m):
            return False  # a cross-reference in words ("par. 4"), not the marker or an arrow
        tdoc, frag = self.bk.link(a.get("href") or "", doc)
        t = self.bk.ids.get(tdoc, {}).get(frag) if tdoc and frag else None
        near = self.near.get(id(note), set())
        while t is not None and t.name in INLINE and isinstance(t.parent, Tag):
            if id(t) in near:
                return True
            t = t.parent
        return t is not None and id(t) in near


def marker(a: Tag) -> str:
    """A note link's text without its brackets: `{721}` -> `721`, `[1]` -> `1`."""
    return re.sub(r"\s+", " ", a.get_text()).strip().strip("[](){}").strip()


class Walker:
    """Blocks of the reading flow (or, `note` set, of one note's body) from the documents' elements."""

    def __init__(self, bk: Book, notes: Notes, img_dir: Path, note: Tag | None = None) -> None:
        self.bk, self.notes, self.img_dir, self.note = bk, notes, img_dir, note
        self.blocks: list[dict] = []
        self.chapters: list[dict] = []
        self.pending: list[dict] = []
        self.gap = 0  # empty paragraphs since the last block: blank lines in the book
        self.sheet = extract_style.Sheet([])
        self.doc = ""
        self.stanza_n = 0
        self.prefix: str | None = None  # a list item's number or bullet, for its first block
        self.starts: dict[int, list[tuple[str, int]]] = {}  # id(el) -> table of contents entries starting there
        self.toc = bool(bk.toc) and note is None
        self.block_memo: dict[int, bool] = {}
        self.pre: Tag | None = None  # the <pre> whose blocks are being read: their line ends stay
        self.waiting: list[dict] = []  # note links of a paragraph holding nothing else, before any block

    # ---- hooks for inline_text
    def skip(self, el: Tag) -> bool:
        if el.name in SKIP or el is self.note:
            return el.name in SKIP
        if id(el) in self.notes.containers or boilerplate(el):
            return True
        return self.note is not None and el.name == "a" and self.notes.backlink(el, self.doc, self.note)

    def note_ref(self, a: Tag):
        return None if self.note is not None else self.notes.links.get(id(a))

    def image(self, el: Tag) -> str | None:
        return self.bk.image(el, self.doc, self.img_dir)

    # ---- structure
    def own(self, el: Tag) -> dict:
        return self.sheet.own(el.name, classes(el), el.get("style"))

    def has_block(self, el: Tag) -> bool:
        k = id(el)
        if k not in self.block_memo:
            self.block_memo[k] = any(
                isinstance(c, Tag) and (c.name in BLOCK_TAGS or self.has_block(c)) for c in el.children
            )
        return self.block_memo[k]

    def is_block(self, el: Tag) -> bool:
        return el.name in BLOCK_TAGS or self.has_block(el)

    def open_chapter(self, title: str, level: int) -> None:
        self.trail()
        self.gap = 0
        self.chapters.append({"id": f"s{len(self.chapters)}", "title": title, "level": level,
                              "first_block": len(self.blocks)})  # fmt: skip

    def trail(self) -> None:
        """Pictures waiting for a block at a chapter's (or the book's) end close the block before them."""
        if self.pending and self.blocks:
            self.blocks[-1]["images"] += [{**im, "after": True} for im in self.pending]
            self.pending.clear()

    def open_at(self, el: Tag, deep: bool) -> None:
        if not self.starts:
            return
        for x in [el, *el.find_all(True)] if deep else [el]:
            for title, level in self.starts.pop(id(x), ()):
                self.open_chapter(title, level)

    def at_chapter_start(self) -> bool:
        return bool(self.chapters) and all(
            b["kind"] in ("title", "subtitle") for b in self.blocks[self.chapters[-1]["first_block"] :]
        )

    def enter(self, el: Tag, mode: tuple[str, int | None]) -> tuple[str, int | None]:
        """The kind (and stanza) of the paragraphs inside a container."""
        kind, stanza = mode
        cls, types = " ".join(classes(el)), tokens(el, "epub:type")
        if re.search(r"verse|stanza|poem", cls) or types & VERSE_TYPES:
            kind = "verse"
        elif "epigraph" in cls or "epigraph" in types:
            kind = "epigraph"
        elif "text-author" in cls:
            kind = "author"
        elif el.name == "blockquote" and kind == "p":
            kind = "epigraph" if self.note is None and self.at_chapter_start() else "cite"
        if "stanza" in cls:
            self.stanza_n += 1
            stanza = self.stanza_n
        return kind, stanza

    def walk(self, el: Tag, ctx: dict, mode: tuple[str, int | None]) -> None:
        outer = self.pre
        if el.name == "pre":
            self.pre = el
        run: list = []
        n = int(m.group(1)) if el.name == "ol" and (m := INT.fullmatch(el.get("start") or "")) else 1
        for c in list(el.children):
            if isinstance(c, Tag) and self.is_block(c):
                self.flush(run, ctx, mode)
                run = []
                if c.name == "li" and el.name in ("ol", "ul", "menu", "dir"):
                    if el.name == "ol":
                        if m := INT.fullmatch(c.get("value") or ""):
                            n = int(m.group(1))
                        self.prefix = f"{n}. "
                        n += 1
                    else:
                        self.prefix = "• "
                    self.block(c, ctx, mode)
                    self.prefix = None
                else:
                    self.block(c, ctx, mode)
            elif isinstance(c, Tag) or is_text(c):
                run.append(c)
        self.flush(run, ctx, mode)
        self.pre = outer

    def flush(self, run: list, ctx: dict, mode: tuple[str, int | None]) -> None:
        """Text and inline elements between blocks: a paragraph of the container's kind."""
        for x in run:
            if isinstance(x, Tag) and not self.skip(x):
                self.open_at(x, True)
        if run:
            self.add(None, mode[0], ctx, mode, nodes=run)

    def block(self, c: Tag, ctx: dict, mode: tuple[str, int | None]) -> None:
        n = c.name
        if self.skip(c):
            return
        if n == "hr":
            self.open_at(c, False)
            return
        if n in HEADINGS or n in ("h5", "h6"):
            self.open_at(c, True)
            r = self.inline(c)
            if n in HEADINGS and self.note is None:
                self.gap = 0  # a blank line before a chapter is not the chapter's
                title = join_title(r["text"].split("\n"))
                if not self.toc and title:  # a heading of nothing but a picture opens no chapter
                    self.open_chapter(title, HEADINGS[n])
            self.add(c, "title" if n in HEADINGS and self.note is None else "subtitle", ctx, mode, r=r)
        elif n == "p":
            self.open_at(c, True)
            cls, types = " ".join(classes(c)), tokens(c, "epub:type")
            kind = (
                "verse" if re.search(r"verse|stanza|poem", cls) or types & VERSE_TYPES
                else "epigraph" if "epigraph" in cls or "epigraph" in types
                else "author" if "text-author" in cls
                else mode[0]
            )  # fmt: skip
            self.add(c, kind, ctx, mode)
        elif n == "table" and self.data_table(c):
            self.open_at(c, True)
            self.table(c, ctx)
        else:
            self.open_at(c, False)
            inner = self.enter(c, mode)
            if self.has_block(c):
                self.walk(c, extract_style.inherit(ctx, self.own(c)), inner)
            else:
                self.open_at(c, True)
                self.add(c, inner[0], ctx, inner)

    def inline(self, el: Tag | None, nodes=None, cell: bool = False) -> dict:
        if self.pre is not None:  # inside a <pre>: read as its text, line ends kept
            return inline_text(self.pre, self, cell=cell, nodes=list(el.children) if nodes is None else nodes)
        return inline_text(el, self, cell=cell, nodes=nodes)

    def add(self, el: Tag | None, kind: str, ctx: dict, mode, nodes=None, r: dict | None = None) -> None:
        if r is None:
            r = self.inline(el, nodes)
        text = r["text"]
        if not text.strip():
            self.pending += [{"src": p["src"]} for p in r["pics"]]
            if r["notes"]:
                self.anchor(r["notes"])
            elif el is not None and el.name == "p" and el.find(["img", "image", "svg"]) is None:
                self.gap += 1
            return
        if kind != "verse":
            lead = len(text) - len(text.lstrip(" "))
            if lead:
                shift(r, -lead)
                text = r["text"] = text[lead:]
            lines = text.split("\n")
            if kind in ("p", "cite") and sum(1 for x in lines if x.strip()) >= 4 and all(len(x) <= 60 for x in lines):
                kind = "verse"  # four short lines or more parted by <br>: a poem (two or three: an address)
        if self.prefix is not None:
            if not LIST_MARK.match(text):
                shift(r, len(self.prefix))
                text = r["text"] = self.prefix + text
            self.prefix = None
        stanza = None
        if kind == "verse":
            if mode[1] is not None:
                stanza = mode[1]
            elif "\n" in text:
                self.stanza_n += 1
                stanza = self.stanza_n
        if self.note is None and not self.chapters:
            self.chapters.append({"id": "s0", "title": "", "level": 1, "first_block": 0})
        r["notes"] = self.take_waiting() + r["notes"]
        b = {
            "images": self.pending.copy(),
            "id": f"b{len(self.blocks)}",
            "kind": kind,
            "chapter": len(self.chapters) - 1,
            "stanza": stanza,
            "text": text,
            "em": r["em"],
            "strong": r["strong"],
            "sup": r["sup"],
            "sub": r["sub"],
            "notes": r["notes"],
            "sentences": block_sentences(text, kind),
            "audio": True,
            "pics": r["pics"],
        }
        self.blocks.append(b)
        if self.note is None:
            st = extract_style.block_style(kind, self.own(el) if el is not None else {}, ctx, self.gap)
            if st:
                b["st"] = st
        self.gap = 0
        self.pending.clear()

    def anchor(self, notes: list[dict]) -> None:
        """Note links of a paragraph with no text of its own: at the end of the block before, else at the start
        of the next one."""
        if self.blocks:
            b = self.blocks[-1]
            b["notes"] += [{**n, "pos": len(b["text"])} for n in notes]
        else:
            self.waiting += notes

    def take_waiting(self) -> list[dict]:
        got = [{**n, "pos": 0} for n in self.waiting]
        self.waiting = []
        return got

    def data_table(self, t: Tag) -> bool:
        """A table of data (kind "table"), not one that lays a page out: that one is read as its cells' blocks."""
        if tokens(t, "role") & {"presentation", "none"} or any(p.name == "table" for p in t.parents):
            return False
        cells = [c for tr in table_rows(t) for c in tr.children if isinstance(c, Tag) and c.name in ("td", "th")]
        if any(c.find(list(LAYOUT)) is not None for c in cells):
            return False
        heads = {c.name for c in t.children if isinstance(c, Tag)}
        if "caption" in heads or "thead" in heads or any(c.name == "th" for c in cells):
            return True
        rows = table_rows(t)
        cols = max(
            (sum(1 for c in tr.children if isinstance(c, Tag) and c.name in ("td", "th")) for tr in rows), default=0
        )
        return (
            len(rows) >= 2
            and cols >= 2
            and all(len(re.sub(r"\s+", " ", c.get_text()).strip()) <= MAX_CELL for c in cells)
        )

    def table(self, t: Tag, ctx: dict) -> None:
        cap = t.find("caption", recursive=False)
        if cap is not None:
            self.add(cap, "p", ctx, ("p", None))
        trs = table_rows(t)
        r: dict = {"text": "", "em": [], "strong": [], "sup": [], "sub": [], "notes": [], "pics": []}
        out_rows, sentences = [], []
        for tr in trs:
            cells = [c for c in tr.children if isinstance(c, Tag) and c.name in ("td", "th")]
            got = [self.inline(c, cell=True) for c in cells]
            for g in got:
                lead = len(g["text"]) - len(g["text"].lstrip(" "))
                if lead:
                    shift(g, -lead)
                    g["text"] = g["text"][lead:]
            if not any(g["text"] or g["pics"] for g in got):
                continue
            if out_rows:
                r["text"] += "\n"
            row, start = [], len(r["text"])
            for i, (c, g) in enumerate(zip(cells, got, strict=True)):
                if i:
                    r["text"] += "\t"
                base = len(r["text"])
                shift(g, base)
                r["text"] += g["text"]
                for k in ("em", "strong", "sup", "sub", "notes", "pics"):
                    r[k] += g[k]
                row.append([base, len(r["text"]), 1] if c.name == "th" else [base, len(r["text"])])
            out_rows.append(row)
            a, e = start, len(r["text"])
            while a < e and r["text"][a] in "\t ":
                a += 1
            while e > a and r["text"][e - 1] in "\t ":
                e -= 1
            if e > a:
                sentences.append([a, e])
        if not sentences:
            self.pending += [{"src": p["src"]} for p in r["pics"]]
            return
        if self.note is None and not self.chapters:
            self.chapters.append({"id": "s0", "title": "", "level": 1, "first_block": 0})
        r["notes"] = self.take_waiting() + r["notes"]
        b = {"images": self.pending.copy(), "id": f"b{len(self.blocks)}", "kind": "table",
             "chapter": len(self.chapters) - 1, "stanza": None, "text": r["text"], "em": r["em"],
             "strong": r["strong"], "sup": r["sup"], "sub": r["sub"], "notes": r["notes"], "sentences": sentences,
             "audio": False, "pics": r["pics"], "rows": out_rows}  # fmt: skip
        self.blocks.append(b)
        if self.note is None:
            st = extract_style.block_style("table", self.own(t), ctx, self.gap)
            if st:
                b["st"] = st
        self.gap = 0
        self.pending.clear()


def boilerplate(el: Tag) -> bool:
    return (
        any(c.lower() in BOILERPLATE for c in classes(el))
        or (el.get("id") or "").lower() in BOILERPLATE
        or "toc" in tokens(el, "epub:type")
    )


def credits_page(soup: BeautifulSoup) -> bool:
    """Wikisource's "About this digital edition" page, which an export adds at its end."""
    title = soup.find("title")
    return (title is not None and title.get_text().strip() == "MediaWiki:Wsexport_about") or soup.find(
        id="ws-contributor"
    ) is not None


def table_rows(t: Tag) -> list[Tag]:
    """A table's own rows, in order (not those of a table inside it)."""
    out: list[Tag] = []

    def rows(el: Tag) -> None:
        for c in el.children:
            if isinstance(c, Tag):
                if c.name == "tr":
                    out.append(c)
                elif c.name != "table":
                    rows(c)

    rows(t)
    return out


def shift(r: dict, d: int) -> None:
    """Move every offset of an inline result by `d` (its text changes apart: cut or prefixed by the caller)."""
    for k in ("em", "strong", "sup", "sub"):
        r[k] = [[max(a + d, 0), b + d] for a, b in r[k] if b + d > max(a + d, 0)]
    for k in ("notes", "pics"):
        r[k] = [{**n, "pos": max(n["pos"] + d, 0)} for n in r[k]]


NOTE_KIND = {"title": "subtitle", "table": "p"}


def leading_backlink(w: Walker) -> bool:
    """Whether a note's first text is in a back link, which its walker leaves out."""

    def first(el: Tag, inside: bool) -> bool | None:
        for c in el.children:
            if is_text(c):
                if c.strip():
                    return inside
            elif isinstance(c, Tag):
                got = first(c, inside or (c.name == "a" and w.notes.backlink(c, w.doc, w.note)))
                if got is not None:
                    return got
        return None

    return w.note is not None and bool(first(w.note, False))


def note_body(w: Walker) -> dict:
    """A note's blocks as one text: paragraphs parted by a blank line, the lines of one stanza by a line break."""
    o: dict = {"text": "", "em": [], "strong": [], "sup": [], "sub": [], "pics": [], "kinds": []}
    prev = None
    for b in w.blocks:
        kind = NOTE_KIND.get(b["kind"], b["kind"])
        sep = "" if prev is None else "\n" if kind == prev[0] == "verse" and b["stanza"] == prev[1] else "\n\n"
        base = len(o["text"]) + len(sep)
        o["text"] += sep + b["text"]
        for k in ("em", "strong", "sup", "sub"):
            o[k] += [[a + base, e + base] for a, e in b[k]]
        o["pics"] += [{"pos": base, "src": im["src"]} for im in b["images"]]
        o["pics"] += [{"pos": p["pos"] + base, "src": p["src"]} for p in b["pics"]]
        if kind != "p":
            k = o["kinds"]
            if sep == "\n" and k and k[-1][2] == "verse" and k[-1][1] == base - 1:
                k[-1][1] = len(o["text"])
            else:
                k.append([base, len(o["text"]), kind])
        prev = (kind, b["stanza"])
    o["pics"] += [{"pos": len(o["text"]), "src": im["src"]} for im in w.pending]
    # a number left of a removed back link: "1. Text" -> ". Text"; a note's own "..." stays
    cut = len(o["text"]) - len(o["text"].lstrip(".)]: ")) if leading_backlink(w) else 0
    if cut:
        o["text"] = o["text"][cut:]
        for k in ("em", "strong", "sup", "sub"):
            o[k] = [[max(a - cut, 0), e - cut] for a, e in o[k] if e - cut > max(a - cut, 0)]
        o["pics"] = [{**p, "pos": max(p["pos"] - cut, 0)} for p in o["pics"]]
        o["kinds"] = [[max(a - cut, 0), e - cut, k] for a, e, k in o["kinds"] if e - cut > max(a - cut, 0)]
    return o


def extract(path: Path) -> dict:
    bk = Book(path)
    img_dir = path.parent / "images"
    notes = Notes(bk)
    w = Walker(bk, notes, img_dir)
    starts: dict[str, list] = {}
    for doc, frag, title, level in bk.toc:
        el = bk.ids[doc].get(frag) if frag else None
        if el is not None:
            w.starts.setdefault(id(el), []).append((title, level))
        else:
            starts.setdefault(doc, []).append((title, level))
    css_files: dict[str, str] = {}

    def stylesheets(soup: BeautifulSoup, doc: str) -> list[str]:
        """The document's CSS in order: linked files and <style> elements."""
        out = []
        for el in soup.find_all(["link", "style"]):
            if el.name == "style":
                out.append("".join(str(s) for s in el.children if isinstance(s, NavigableString)))
                continue
            rel = el.get("rel") or []
            rel = " ".join(rel) if isinstance(rel, list) else rel
            if "stylesheet" not in rel.lower() or not el.get("href"):
                continue
            full, _ = bk.link(el["href"], doc)
            if full is not None:
                if full not in css_files:
                    css_files[full] = bk.z.read(full).decode("utf-8", "replace")
                out.append(css_files[full])
        return out

    for doc in bk.docs:
        soup = bk.soups[doc]
        body = soup.body or soup
        w.doc = doc
        w.sheet = extract_style.Sheet(stylesheets(soup, doc) if extract_style.ENABLED else [])
        w.gap = 0
        for title, level in starts.get(doc, ()):
            w.open_chapter(title, level)
        w.walk(body, extract_style.inherit({}, w.own(body)), ("p", None))
    w.trail()
    if not w.chapters:
        w.chapters.append({"id": "s0", "title": "", "level": 1, "first_block": 0})
    book_notes: dict = {}
    for nid, c, cdoc, _links in notes.list:
        nw = Walker(bk, notes, img_dir, note=c)
        nw.doc = cdoc
        nw.block(c, {}, ("p", None))
        book_notes[nid] = note_body(nw)
    if bk.cover:
        img_dir.mkdir(exist_ok=True)
        ext = posixpath.splitext(bk.cover)[1].lower() or ".jpg"
        (img_dir / f"cover{ext}").write_bytes(bk.z.read(bk.cover))
    return {
        "title": bk.title or path.stem,
        "author": bk.author,
        "chapters": w.chapters,
        "blocks": w.blocks,
        "notes": book_notes,
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
    (args.book_dir / "book.json").write_text(dump_book(book), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
