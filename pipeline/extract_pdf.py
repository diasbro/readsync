"""Extract a PDF into the book.json model. A PDF states glyphs at positions, not paragraphs: the text layer
is read glyph by glyph with each glyph's place, size and weight, and the book is rebuilt from that.

- lines: glyphs on one baseline, a space where the gap between two glyphs is wider than a letter's spacing;
  a font whose ToUnicode map contradicts its own glyph names (`/uni0411` read as "б") is read by the names
- furniture: page numbers (a lone number at a page's top or foot that counts on with the pages around it, not
  in a larger font or over a heading) and running heads (lines a page repeats from its neighbours) are dropped,
  and so is a table of contents (lines ending in dot leaders and a page number; a page mostly of them among the
  first tenth of the pages goes whole)
- footnotes: lines in a smaller font at a page's foot, after a gap, each starting with its marker (`*`, `**`,
  `1`), become notes linked where the marker stands in the text: a number glued to a lower-case letter or to
  punctuation (not CO2, т.1), in the order of the notes; a note whose marker the text does not show is linked
  where the page's text ends
- headings: lines in a larger or bold font (or "Глава 1" alone on a line), alone between paragraphs; a
  heading that is larger, centred, in capitals or opens its page is a chapter, any other a subtitle
- paragraphs: a new one where the vertical gap is wider than the line spacing or the first line is
  indented; a book with neither ends a paragraph at a short line that ends a sentence
- a word broken by a hyphen at a line end is joined, a real hyphen (`чем-то`, `Лао-цзы`) is kept

The phone's importer (ios/Sources/Import/PDF.swift) follows the same rules on the lines PDFKit gives."""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_text import block_sentences, dump_book  # noqa: E402

PAGE_NO = re.compile(r"^[\[\-–—\s]*(?:([0-9]{1,4})|([ivxlcdm]{1,7})|стр\.?\s*([0-9]{1,4}))[\]\-–—\s.]*$", re.I)
ROMAN = re.compile(r"^m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$")
ROMAN_VALUE = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
ENDS_SENTENCE = re.compile(r"[.!?…:][»”\"')\]]*$")
REPEATS = 0.5  # a line on this share of the pages is a running head, not text
NEAR = (-2, -1, 1, 2)  # the pages a page number is checked against: one without (a chapter's first) may stand between
TOC_LINE = re.compile(r"^\S.*?(?:(?:\s?\.){4,}|(?:\s?…){2,})\s*[0-9]{1,4}$")  # dot leaders and a page number
TOC_FRONT = 10  # a page of contents goes whole only among the first 1/TOC_FRONT of the pages
KEYWORD_HEADING = re.compile(r"^(?:Глава|ГЛАВА|Часть|ЧАСТЬ|Пролог|Эпилог|Chapter|CHAPTER|Part|PART)\b[^.!?,;:]{0,40}$")
NOTE_START = re.compile(r"^(\*{1,4})\s*(?=[^\s*])|^([0-9]{1,3})(?:[.)]\s*|\s+)(?=\S)")
SCENE_BREAK = re.compile(r"^(?:\* ?){3,}$")  # `* * *` in a small font at a page's foot is a break, not a note
GLUED_AFTER = "»”\"')]"  # a note number may follow these, or a lower-case letter, or .,;:!?
ABBREVIATION = 3  # letters before a full stop that make the number after it a reference (т.1, гл.2), not a note
HYPHENS = "-\u2010\u2011\u00ad"
# the second part of a word that keeps its hyphen at a line end: чем-то, кто-либо, где-нибудь, всё-таки
PARTICLES = {"то", "либо", "нибудь", "таки"}
FIRST_PARTS = {"кое", "кой"}  # кое-как, кой-где
WORD = re.compile(r"[^\W\d_]+(?:[-\u2010\u2011][^\W\d_]+)*")
MARK = 0xF0000  # note markers in the text until the paragraphs are built: code points from here on that the
# book's own text does not use (Supplementary Private Use Area)


@dataclass
class Line:
    text: str
    x0: float  # left and right ends of the inked text, in points
    x1: float
    y: float  # baseline, growing up the page as in PDF
    size: float  # the font size most of its characters have
    bold: bool = False  # most of its letters are in a bold face (the phone cannot tell)


# ---------------------------------------------------------------- reading the text layer


def open_pdf(path: Path):
    """The file, opened once: an "owner" lock without a password opens with an empty one, a real
    password is not guessed."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        raise SystemExit("PDF под паролем: сними защиту (например, «Экспорт в PDF» в Просмотре) и добавь снова")
    return reader


def _hex_text(h: str) -> str:
    b = bytes.fromhex(h if len(h) % 2 == 0 else h + "0")
    return b.decode("utf-16-be", "replace") if len(b) >= 2 else b.decode("latin-1")


def parse_tounicode(data: str) -> dict[int, str]:
    """A ToUnicode CMap's bfchar and bfrange entries: code -> text."""
    m: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in re.findall(r"<\s*([0-9A-Fa-f\s]+)>\s*<\s*([0-9A-Fa-f\s]*)>", block):
            m[int(re.sub(r"\s", "", a), 16)] = _hex_text(re.sub(r"\s", "", b))
    for block in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for a, b, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            lo, hi = int(a, 16), int(b, 16)
            if hi - lo > 0xFFFF:
                continue
            if rest.startswith("["):
                for i, h in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                    m[lo + i] = _hex_text(h)
            else:
                base = bytes.fromhex(rest[1:-1]) if len(rest) > 2 else b""
                if not base:
                    continue
                last = int.from_bytes(base[-2:] if len(base) >= 2 else base, "big")
                head = base[:-2] if len(base) >= 2 else b""
                if last + hi - lo > 0xFFFF:  # runs past what two bytes hold: this range is broken, not the map
                    continue
                for i in range(hi - lo + 1):
                    m[lo + i] = (head + (last + i).to_bytes(2, "big")).decode("utf-16-be", "replace")
    return m


def glyph_text(name: str) -> str | None:
    """A glyph name's character: `uni0411`, `u1F600`, or the Adobe Glyph List."""
    n = name.lstrip("/").split(".", 1)[0]
    if m := re.fullmatch(r"uni((?:[0-9A-F]{4})+)", n):
        return "".join(chr(int(m[1][i : i + 4], 16)) for i in range(0, len(m[1]), 4))
    if m := re.fullmatch(r"u([0-9A-F]{4,6})", n):
        return chr(int(m[1], 16))
    try:
        from pypdf._codecs import adobe_glyphs

        return adobe_glyphs.get("/" + n)
    except ImportError:  # a pypdf without its glyph list: only uniXXXX names are read
        return None


def _base_encoding(name: str) -> list[str]:
    codec = {"/WinAnsiEncoding": "cp1252", "/MacRomanEncoding": "mac_roman"}.get(name, "latin-1")
    out = []
    for c in range(256):
        try:
            out.append(bytes([c]).decode(codec))
        except UnicodeDecodeError:
            out.append(chr(c))
    return out


class Font:
    """What text extraction needs of a font: code -> text, code -> advance width, and whether it is bold."""

    def __init__(self, fd) -> None:
        fd = fd.get_object()
        self.subtype = str(fd.get("/Subtype", ""))
        name = str(fd.get("/BaseFont", ""))
        desc = fd.get("/FontDescriptor")
        if self.subtype == "/Type0" and fd.get("/DescendantFonts"):
            desc = fd["/DescendantFonts"][0].get_object().get("/FontDescriptor") or desc
        desc = desc.get_object() if desc is not None else {}
        weight = float(desc.get("/FontWeight", 0) or 0)
        self.bold = bool(re.search(r"Bold|Black|Heavy|Semibold|SemiBold|Demi", name)) or weight >= 600
        self.two_byte = self.subtype == "/Type0"
        self.scale = 0.001
        if self.subtype == "/Type3" and fd.get("/FontMatrix"):
            self.scale = float(fd["/FontMatrix"][0])
        self.widths: dict[int, float] = {}
        self.uni: dict[int, str] = {}
        tu = fd.get("/ToUnicode")
        if tu is not None:
            try:
                self.uni = parse_tounicode(tu.get_object().get_data().decode("latin-1"))
            except Exception:  # a broken map: fall back to the encoding
                self.uni = {}
        if self.two_byte:
            self._cid_widths(fd)
            enc = fd.get("/Encoding")
            enc = enc.get_object() if enc is not None else None
            if enc is not None and hasattr(enc, "get_data"):
                ranges = enc.get_data().split(b"endcodespacerange")[0]
                spaces = re.findall(rb"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]+>", ranges)
                self.two_byte = not spaces or any(len(s) > 2 for s in spaces)
            return
        missing = float(desc.get("/MissingWidth", 0) or 0)
        self.default = missing if fd.get("/Widths") else (missing or 500.0)  # no widths at all: a standard font
        first = int(fd.get("/FirstChar", 0) or 0)
        for i, w in enumerate(fd.get("/Widths") or []):
            self.widths[first + i] = float(w)
        enc = fd.get("/Encoding")
        enc = enc.get_object() if enc is not None else None
        base = _base_encoding(str(enc.get("/BaseEncoding", "")) if hasattr(enc, "get") else str(enc or ""))
        names: dict[int, str] = {}
        if hasattr(enc, "get"):
            code = 0
            for d in enc.get("/Differences") or []:
                if isinstance(d, int) or (hasattr(d, "as_numeric") and not str(d).startswith("/")):
                    code = int(d)
                    continue
                names[code] = str(d)
                code += 1
        for code in range(256):
            if code in names:
                g = glyph_text(names[code])
                if g and (code not in self.uni or re.fullmatch(r"/uni[0-9A-F]{4}", names[code])):
                    # a uniXXXX glyph name states its character: it wins over a ToUnicode map that
                    # contradicts it (InDesign has written capitals as their lower case)
                    self.uni[code] = g
            if code not in self.uni:
                self.uni[code] = base[code]

    def _cid_widths(self, fd) -> None:
        d = fd["/DescendantFonts"][0].get_object() if fd.get("/DescendantFonts") else {}
        self.default = float(d.get("/DW", 1000) or 1000)
        w = d.get("/W") or []
        i = 0
        while i < len(w):
            first = int(w[i])
            nxt = w[i + 1].get_object() if hasattr(w[i + 1], "get_object") else w[i + 1]
            if isinstance(nxt, list):
                for k, v in enumerate(nxt):
                    self.widths[first + k] = float(v)
                i += 2
            else:
                for c in range(first, int(nxt) + 1):
                    self.widths[c] = float(w[i + 2])
                i += 3

    def codes(self, data: bytes) -> list[tuple[int, str, float]]:
        """(code, text, advance width in text space units at size 1) for each glyph of a string."""
        out = []
        step = 2 if self.two_byte else 1
        for i in range(0, len(data) - step + 1, step):
            c = int.from_bytes(data[i : i + step], "big")
            t = self.uni.get(c)
            if t is None:
                t = "" if self.two_byte else chr(c)
            out.append((c, t, self.widths.get(c, self.default) * self.scale))
        return out


def _mul(m: list[float], n: list[float]) -> list[float]:
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return [
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    ]


def _raw(s) -> bytes:
    """A string operand's bytes: pypdf may have decoded it to text, its bytes are kept alongside."""
    if isinstance(s, bytes):
        return bytes(s)
    return bytes(getattr(s, "original_bytes", None) or s.encode("latin-1", "replace"))


@dataclass
class Glyph:
    text: str
    x0: float
    x1: float
    y: float
    size: float
    bold: bool


def page_glyphs(page, reader, fonts: dict | None = None) -> list[Glyph]:
    """Every upright glyph a page shows, in the order the page draws them. `fonts` caches the document's fonts
    by object number across its pages (a font's maps are parsed once)."""
    from pypdf.generic import ContentStream

    out: list[Glyph] = []
    shared = fonts if fonts is not None else {}
    own: dict = {}  # fonts written into this page's resources, not as objects of their own

    def font_of(res, name) -> Font | None:
        try:
            ref = res["/Font"].raw_get(name)
        except (KeyError, TypeError, AttributeError):
            return None
        idnum = getattr(ref, "idnum", None)
        cache, key = (shared, (idnum, getattr(ref, "generation", 0))) if idnum is not None else (own, id(ref))
        if key not in cache:
            try:
                cache[key] = Font(ref)
            except Exception:  # a font this reader cannot make sense of: its text is skipped
                cache[key] = None
        return cache[key]

    def run(contents, res, ctm: list[float], depth: int) -> None:
        if contents is None or depth > 6:
            return
        try:
            ops = ContentStream(contents, reader).operations
        except Exception:  # a damaged content stream: whatever came before it stays
            return
        res = res.get_object() if res is not None else {}
        stack: list[tuple] = []
        tm = [1.0, 0, 0, 1, 0, 0]
        tlm = list(tm)
        font: Font | None = None
        size, tc, tw, th, tl, rise = 0.0, 0.0, 0.0, 1.0, 0.0, 0.0

        def show(data: bytes) -> None:
            nonlocal tm
            if font is None:
                return
            for code, t, w in font.codes(data):
                trm = _mul([size * th, 0, 0, size, 0, rise], _mul(tm, ctm))
                tx = (w * size + tc + (tw if not font.two_byte and code == 32 else 0)) * th
                if abs(trm[1]) < 1e-6 and abs(trm[2]) < 1e-6 and trm[0] > 0 and trm[3] > 0 and t:
                    out.append(Glyph(t, trm[4], trm[4] + w * trm[0], trm[5], trm[3], font.bold))
                tm = _mul([1, 0, 0, 1, tx, 0], tm)

        for operands, op in ops:
            try:
                if op == b"q":
                    stack.append((list(ctm), font, size, tc, tw, th, tl, rise))
                elif op == b"Q" and stack:
                    ctm, font, size, tc, tw, th, tl, rise = stack.pop()
                elif op == b"cm":
                    ctm = _mul([float(x) for x in operands], ctm)
                elif op == b"BT":
                    tm, tlm = [1.0, 0, 0, 1, 0, 0], [1.0, 0, 0, 1, 0, 0]
                elif op == b"Tf":
                    font, size = font_of(res, operands[0]), float(operands[1])
                elif op == b"Tc":
                    tc = float(operands[0])
                elif op == b"Tw":
                    tw = float(operands[0])
                elif op == b"Tz":
                    th = float(operands[0]) / 100
                elif op == b"TL":
                    tl = float(operands[0])
                elif op == b"Ts":
                    rise = float(operands[0])
                elif op in (b"Td", b"TD"):
                    if op == b"TD":
                        tl = -float(operands[1])
                    tlm = _mul([1, 0, 0, 1, float(operands[0]), float(operands[1])], tlm)
                    tm = list(tlm)
                elif op == b"Tm":
                    tlm = [float(x) for x in operands]
                    tm = list(tlm)
                elif op == b"T*":
                    tlm = _mul([1, 0, 0, 1, 0, -tl], tlm)
                    tm = list(tlm)
                elif op == b"Tj":
                    show(_raw(operands[0]))
                elif op in (b"'", b'"'):
                    if op == b'"':
                        tw, tc = float(operands[0]), float(operands[1])
                    tlm = _mul([1, 0, 0, 1, 0, -tl], tlm)
                    tm = list(tlm)
                    show(_raw(operands[-1]))
                elif op == b"TJ":
                    for item in operands[0]:
                        if isinstance(item, (int, float)) or hasattr(item, "as_numeric"):
                            tm = _mul([1, 0, 0, 1, -float(item) / 1000 * size * th, 0], tm)
                        else:
                            show(_raw(item))
                elif op == b"Do":
                    xo = res["/XObject"][operands[0]].get_object()
                    if xo.get("/Subtype") == "/Form":
                        m = [float(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                        run(xo, xo.get("/Resources") or res, _mul(m, ctm), depth + 1)
            except (KeyError, ValueError, TypeError, IndexError, AttributeError):
                continue  # one malformed operator does not cost the rest of the page

    run(page.get_contents(), page.get("/Resources"), [1.0, 0, 0, 1, 0, 0], 0)
    return out


def glyph_lines(glyphs: list[Glyph]) -> list[Line]:
    """Glyphs grouped by baseline (a raised marker stays on its line), top to bottom, each line left to right
    with a space where two glyphs stand further apart than letters do."""
    rows: list[list[Glyph]] = []
    for g in glyphs:
        if not g.text.strip() and not rows:
            continue
        for row in reversed(rows[-8:]):
            ref = max(row, key=lambda r: r.size)
            if abs(g.y - ref.y) < 0.5 * max(ref.size, g.size):
                row.append(g)
                break
        else:
            rows.append([g])
    lines = []
    for row in rows:
        row.sort(key=lambda r: r.x0)
        sizes: Counter[float] = Counter()
        bold = letters = 0
        parts: list[str] = []
        prev: Glyph | None = None
        for g in row:
            if prev is not None and g.x0 - prev.x1 > 0.15 * max(g.size, prev.size) and parts and parts[-1][-1:] != " ":
                parts.append(" ")
            parts.append(g.text)
            if g.text.strip():
                sizes[round(g.size * 2) / 2] += len(g.text)
                if g.text.isalpha():
                    letters += 1
                    bold += g.bold
                prev = g
        inked = [g for g in row if g.text.strip()]
        if not inked:
            continue
        size = sizes.most_common(1)[0][0]
        base = Counter(round(g.y, 1) for g in inked if round(g.size * 2) / 2 == size).most_common(1)[0][0]
        lines.append(Line("".join(parts), inked[0].x0, inked[-1].x1, base, size, letters > 0 and bold * 2 > letters))
    lines.sort(key=lambda ln: (-round(ln.y, 0), ln.x0))
    return lines


def page_lines(reader) -> list[list[Line]]:
    fonts: dict = {}
    return [glyph_lines(page_glyphs(p, reader, fonts)) for p in reader.pages]


# ---------------------------------------------------------------- from lines to a book


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def text_lines(pages: list) -> list[list[Line]]:
    """Pages as Line lists. A page may be given as plain text (lines without geometry: every line starts at the
    margin, one character is 5 points wide); empty lines go."""
    out = []
    for page in pages:
        if isinstance(page, str):
            page = page.split("\n")
        lines = []
        for i, ln in enumerate(page):
            if isinstance(ln, str):
                t = _clean(ln)
                ln = Line(t, 0.0, 5.0 * len(t), 1000.0 - 12.0 * i, 10.0)
            else:
                ln = Line(_clean(ln.text), ln.x0, ln.x1, ln.y, ln.size, ln.bold)
            if ln.text:
                lines.append(ln)
        out.append(lines)
    return out


def _key(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower())


def page_number(text: str) -> int | None:
    """The number a line that is only a number (`12`, `- 12 -`, `xiv`, `стр. 12`) states; None for any other line,
    and for letters that only look like a Roman number (civil, mild, dim)."""
    m = PAGE_NO.match(text)
    if not m:
        return None
    if m[2] is None:
        return int(m[1] or m[3])
    t = m[2].lower()
    if not ROMAN.match(t):
        return None
    v = [ROMAN_VALUE[c] for c in t]
    return sum(-x if i + 1 < len(v) and x < v[i + 1] else x for i, x in enumerate(v))


def _folio(page: list[Line], i: int, body: float) -> int | None:
    """The page number the line at the top (i = 0) or the foot (i = -1) of a page may be: not one in a larger font
    than the text, nor one right over a heading (a chapter's numeral)."""
    if not page:
        return None
    ln = page[i]
    v = page_number(ln.text)
    if v is None or ln.size >= 1.15 * body:
        return None
    if i == 0 and len(page) > 1 and (page[1].size >= 1.15 * body or page[1].bold):
        return None
    return v


def strip_furniture(pages: list) -> list:
    """Page numbers and the running head or foot, the lines a page repeats from its neighbours. A lone number is a
    page number when it counts on with the pages around it at the same place (top or foot). Pages given as text
    come back as lists of text lines, pages of Lines as Lines."""
    as_text = all(isinstance(p, str) or (p and isinstance(p[0], str)) for p in pages)
    lined = text_lines(pages)
    sizes: Counter[float] = Counter()
    for page in lined:
        for ln in page:
            sizes[ln.size] += len(ln.text)
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    edges: Counter[str] = Counter()
    for page in lined:
        for t in {ln.text for ln in page[:1] + page[-1:]}:
            if page_number(t) is None:  # a number is told by its sequence, not by its repeating
                edges[_key(t)] += 1
    running = {k for k, n in edges.items() if n >= max(3, len(lined) * REPEATS)}
    keep = [list(page) for page in lined]

    def heads() -> None:
        for page in keep:
            for i in (0, -1):
                while page and _key(page[i].text) in running:
                    page.pop(i)

    heads()
    for i in (0, -1):
        nums = [_folio(page, i, body) for page in keep]
        drop = [
            pi
            for pi, v in enumerate(nums)
            if v is not None
            and any(0 <= pi + d < len(nums) and nums[pi + d] is not None and nums[pi + d] - v == d for d in NEAR)
        ]
        for pi in drop:
            keep[pi].pop(i)
    heads()  # a running head under or over the number
    return [[ln.text for ln in page] if as_text else page for page in keep]


def strip_toc(pages: list[list[Line]]) -> list[list[Line]]:
    """A table of contents goes: its entries (dot leaders, a page number) anywhere, and a page among the first
    tenth of the book whose lines are mostly entries, its heading too."""
    out = []
    front = max(1, (len(pages) + TOC_FRONT - 1) // TOC_FRONT)
    for pi, page in enumerate(pages):
        n = sum(1 for ln in page if TOC_LINE.match(ln.text))
        out.append([] if pi < front and n * 2 > len(page) else [ln for ln in page if not TOC_LINE.match(ln.text)])
    return out


def _mode(values: list[float], default: float) -> float:
    return Counter(values).most_common(1)[0][0] if values else default


@dataclass
class Layout:
    body: float  # the text's font size
    leading: float  # baseline to baseline within a paragraph
    left: list[float]  # per page: the text column's left edge
    right: list[float]  # per page: where full lines end


def layout(pages: list[list[Line]]) -> Layout:
    sizes: Counter[float] = Counter()
    for page in pages:
        for ln in page:
            sizes[ln.size] += len(ln.text)
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    gaps = []
    for page in pages:
        for a, b in zip(page, page[1:], strict=False):
            if a.size == b.size == body and 0.8 * body < a.y - b.y < 2.5 * body:
                gaps.append(round((a.y - b.y) * 2) / 2)
    leading = _mode(gaps, 1.2 * body)
    lefts, rights = [], []
    for parity in (0, 1):
        body_lines = [ln for i, p in enumerate(pages) if i % 2 == parity for ln in p if ln.size == body]
        xs = sorted(ln.x1 for ln in body_lines)
        # the margin: the leftmost place many lines start at (the most common one may be the indent, in a book of
        # short paragraphs)
        starts = Counter(round(ln.x0) for ln in body_lines)
        lefts.append(min((x for x, n in starts.items() if n >= 0.15 * len(body_lines)), default=0.0))
        rights.append(xs[int(len(xs) * 0.9)] if xs else 0.0)
    left = [lefts[i % 2] for i in range(len(pages))]
    right = [rights[i % 2] for i in range(len(pages))]
    return Layout(body, leading, left, right)


def _digit(c: str) -> bool:
    return "0" <= c <= "9"


def _glued_number(text: str, i: int) -> bool:
    """A number at `i` stands as a note's marker: after a lower-case letter (слово2, not CO2, B12, MP3), a closing
    quote or bracket, or punctuation, but not after the full stop of a short abbreviation (т.1, гл.2, стр.5)."""
    c = text[i - 1]
    if c.islower() or c in GLUED_AFTER:
        return True
    if c not in ".,;:!?":
        return False
    if c == ".":
        j = i - 1
        while j > 0 and text[j - 1].isalpha():
            j -= 1
        return not 1 <= i - 1 - j <= ABBREVIATION
    return True


def _joined_marker(text: str, marker: str) -> tuple[int, int] | None:
    """Where a footnote's marker stands in the text (start, end): `*` glued to the word or bracket before it, a
    number glued as `_glued_number` says and not part of a longer number."""
    star = marker.startswith("*")
    i = text.find(marker)
    while i >= 0:
        e = i + len(marker)
        if star:
            if i > 0 and not text[i - 1].isspace() and text[i - 1] != "*" and (e == len(text) or text[e] != "*"):
                return i, e
        elif i > 0 and not _digit(text[i - 1]) and (e == len(text) or not _digit(text[e])) and _glued_number(text, i):
            return i, e
        i = text.find(marker, i + 1)
    return None


def _sentinels(pages: list[list[Line]]):
    """Code points to stand for note markers in the text: from MARK on, any the book's own text uses skipped."""
    used = {c for page in pages for ln in page for c in ln.text if ord(c) >= MARK}
    c = MARK
    while True:
        if chr(c) not in used and c & 0xFFFE != 0xFFFE:
            yield chr(c)
        c += 1


def split_notes(pages: list[list[Line]], lay: Layout) -> tuple[list[list[Line]], list[tuple[str, str, str]]]:
    """Footnotes out of the pages: at a page's foot, after a gap, lines in a smaller font, each note starting with
    its marker. The marker in the text above becomes the note's sentinel (a code point the text does not use); a
    note whose marker the text does not show is linked where the page's text ends; a numbered note comes in the
    order of the notes (1, or one more than the last), else its line goes on with the previous note, as does a
    foot that starts without a marker. Returns the pages without them and (marker, text, sentinel) per note."""
    notes: list[list[str]] = []  # per note: marker, sentinel, then its lines
    out = []
    marks = _sentinels(pages)
    last = None  # the last numbered note's number
    open_note = False  # the last page's foot ended mid-sentence: this page's foot may go on with it
    for page in pages:
        k = len(page)
        while k > 0 and page[k - 1].size <= 0.85 * lay.body:
            k -= 1
        body, foot = list(page[:k]), page[k:]
        gap = bool(body and foot and body[-1].y - foot[0].y > 1.3 * lay.leading)
        if not gap or SCENE_BREAK.match(foot[0].text) or not (NOTE_START.match(foot[0].text) or open_note):
            out.append(page)
            open_note = False
            continue
        for ln in foot:
            m = NOTE_START.match(ln.text)
            marker = (m[1] or m[2]) if m else None
            if marker and m[2] and last is not None and int(marker) not in (1, last + 1):
                marker = None  # out of order: a line of the last note that starts with a number
            if not marker:
                if notes:
                    notes[-1].append(ln.text)
                continue
            if m[2]:
                last = int(marker)
            hit = None
            for i, b in enumerate(body):
                if found := _joined_marker(b.text, marker):
                    hit = (i, found)
                    break
            sentinel = next(marks)
            notes.append([marker, sentinel, ln.text[m.end() :]])
            # a marker the text does not show: the note is linked where the page's text ends
            i, (a, e) = hit if hit else (len(body) - 1, (len(body[-1].text),) * 2)
            t = body[i].text
            body[i] = Line(t[:a] + sentinel + t[e:], *_geom(body[i]))
        out.append(body)
        open_note = bool(notes) and not ENDS_SENTENCE.search(notes[-1][-1])
    vocab = vocabulary([[Line(t, 0, 0, 0, 0) for t in n[2:]] for n in notes])
    joined = []
    for n in notes:
        text = n[2]
        for t in n[3:]:
            text = join(text, t, vocab)
        joined.append((n[0], finish(text), n[1]))
    return out, joined


def _geom(ln: Line) -> tuple:
    return ln.x0, ln.x1, ln.y, ln.size, ln.bold


def vocabulary(pages: list[list[Line]]) -> Counter[str]:
    """The book's words as it spells them away from line ends, lower case: a word broken at a line end is
    looked up here to tell a hyphenated compound from a word split for the line."""
    words: Counter[str] = Counter()
    lines = [ln.text for page in pages for ln in page]
    for i, t in enumerate(lines):
        found = WORD.findall(t)
        if found and t.rstrip()[-1:] in HYPHENS:
            found = found[:-1]
        if found and i > 0 and lines[i - 1].rstrip()[-1:] in HYPHENS:
            found = found[1:]
        words.update(w.lower().replace("\u2010", "-").replace("\u2011", "-") for w in found)
    return words


def join(a: str, b: str, vocab: Counter[str]) -> str:
    """Two lines of one paragraph as one text. A hyphen right after a word at the end of `a`, before a
    lower-case letter, is a word broken for the line and goes, unless the word is a hyphenated one: the
    second part is a particle (чем-то, кто-либо), the first is кое/кой, or the book spells the word with
    the hyphen elsewhere more often than without. Before a capital the hyphen stays (Нью-Йорк). A soft
    hyphen always goes; a hyphen after a space is a dash and keeps its spaces."""
    a, b = a.rstrip(), b.lstrip()
    if not a:
        return b
    if not b:
        return a
    m = re.search(r"([^\W\d_]+(?:[-\u2010\u2011][^\W\d_]+)*)([" + HYPHENS + r"])$", a)
    if not m or not b[:1].isalpha():
        return f"{a} {b}"
    head = a[: m.start(2)]
    if m[2] == "\u00ad":
        return head + b
    if not b[:1].islower():
        return head + "-" + b
    keep = keeps_hyphen(m[1], re.match(r"[^\W\d_]+", b)[0], vocab)
    return head + ("-" if keep else "") + b


def keeps_hyphen(left: str, right: str, vocab: Counter[str]) -> bool:
    """A hyphen between `left` (the word before a line end) and `right` (the word that goes on the next line) is
    the word's own: `right` is a particle (чем-то, кто-либо), `left` ends in кое/кой, or the book spells the word
    with the hyphen more often than without. extract_txt joins its lines by the same rule."""
    first = re.split(r"[-\u2010\u2011]", left)[-1].lower()
    whole, hyph = (left + right).lower(), (left + "-" + right).lower()
    return right.lower() in PARTICLES or first in FIRST_PARTS or vocab[hyph] > vocab[whole]


def finish(text: str) -> str:
    """A paragraph's text as the book keeps it: hyphens are plain, soft hyphens and doubled spaces gone."""
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u00ad", "")
    return re.sub(r"\s+", " ", text).strip()


def is_heading(ln: Line, lay: Layout) -> bool:
    """A heading line by its look: a larger or bold face, or a chapter keyword alone on the line."""
    styled = ln.size >= 1.15 * lay.body or ln.bold
    return (styled and len(ln.text) <= 120) or bool(KEYWORD_HEADING.match(ln.text))


@dataclass
class Para:
    text: str
    kind: str  # p, title, subtitle
    size: float


def paragraphs(pages: list[list[Line]], lay: Layout, vocab: Counter[str]) -> list[Para]:
    """Lines into paragraphs and headings. A heading is a run of heading lines in one style that stands apart:
    the line before it ended a paragraph and the line after it starts one."""
    flat = [(pi, ln) for pi, page in enumerate(pages) for ln in page]

    def indent(pi: int, ln: Line) -> float:
        return ln.x0 - lay.left[pi]

    def starts(k: int) -> bool:
        """Line k starts a paragraph by its place: a wide gap above it on its page, or a first-line indent."""
        pi, ln = flat[k]
        if k == 0:
            return True
        ppi, prev = flat[k - 1]
        ratio = lay.leading / lay.body
        if ppi == pi and prev.y - ln.y > 1.4 * ratio * max(prev.size, ln.size):
            return True
        ind = indent(pi, ln)
        if not 0.6 * ln.size < ind < 4 * ln.size:
            return False
        # indented further than the line before, or after a line that ended a sentence short of the margin (a
        # paragraph of one line): lines indented alike are a quotation going on
        return ind > indent(ppi, prev) + 0.6 * ln.size or (
            not _full(prev, lay, ppi) and bool(ENDS_SENTENCE.search(prev.text))
        )

    marks = sum(1 for k in range(1, len(flat)) if starts(k))
    by_place = marks >= 0.02 * len(flat)  # the book shows paragraphs by gaps or indents

    def ends(k: int) -> bool:
        """Line k ends its paragraph by itself: a short line that ends a sentence, where nothing else tells."""
        pi, ln = flat[k]
        return not by_place and not _full(ln, lay, pi) and bool(ENDS_SENTENCE.search(ln.text))

    heading = [is_heading(ln, lay) for _, ln in flat]
    # the cover and title pages: before the first page with a full line of body text their headings are not
    # chapters
    full = [pi for pi, ln in flat if ln.size == lay.body and not ln.bold and _full(ln, lay, pi)]
    front = full[0] if full else 0

    def apart_before(k: int) -> bool:
        if k == 0 or not by_place:
            return True
        return starts(k) or heading[k - 1] or flat[k - 1][0] != flat[k][0] or ends(k - 1)

    def apart_after(j: int) -> bool:
        return j >= len(flat) or not by_place or starts(j) or heading[j] or flat[j][0] != flat[j - 1][0]

    out: list[Para] = []
    buf = ""
    k = 0
    while k < len(flat):
        pi, ln = flat[k]
        if heading[k] and (not buf or not buf.endswith(tuple(HYPHENS))) and apart_before(k):
            j = k + 1  # the heading's other lines: same page, same look, close below
            while (
                j < len(flat)
                and heading[j]
                and flat[j][0] == pi
                and (flat[j][1].size, flat[j][1].bold) == (ln.size, ln.bold)
                and flat[j - 1][1].y - flat[j][1].y < 2.0 * ln.size
                and not KEYWORD_HEADING.match(flat[j][1].text)
            ):
                j += 1
            if apart_after(j):
                if buf:
                    out.append(Para(finish(buf), "p", lay.body))
                    buf = ""
                title = ln.text
                for _, nxt in flat[k + 1 : j]:
                    title = join(title, nxt.text, vocab)
                title = finish(title)
                caps = any(c.isalpha() for c in title) and title == title.upper()
                chapter = (
                    ln.size >= 1.15 * lay.body
                    or _centred(ln, lay, pi)
                    or caps
                    or k == 0
                    or flat[k - 1][0] != pi
                    or bool(KEYWORD_HEADING.match(title))
                )
                out.append(Para(title, "title" if chapter and pi >= front else "subtitle", ln.size))
                k = j
                continue
        if buf and (starts(k) or ends(k - 1)) and not buf.endswith(tuple(HYPHENS)):
            out.append(Para(finish(buf), "p", lay.body))
            buf = ""
        buf = join(buf, ln.text, vocab) if buf else ln.text
        k += 1
    if buf:
        out.append(Para(finish(buf), "p", lay.body))
    return out


def _full(ln: Line, lay: Layout, pi: int) -> bool:
    """A line that reaches the right edge of the text column."""
    return ln.x1 >= lay.right[pi] - 0.08 * (lay.right[pi] - lay.left[pi])


def _centred(ln: Line, lay: Layout, pi: int) -> bool:
    lo, hi = lay.left[pi], lay.right[pi]
    width = max(hi - lo, 1.0)
    return ln.x0 - lo > 0.1 * width and abs((ln.x0 + ln.x1) / 2 - (lo + hi) / 2) < 0.1 * width


def join_lines(pages: list) -> str:
    """The text of pages of lines, paragraphs and headings separated by a blank line (the text the book is
    built from, without notes)."""
    lined = text_lines(pages)
    lay = layout(lined)
    return "\n\n".join(p.text for p in paragraphs(lined, lay, vocabulary(lined)))


def build(paras: list[Para], notes: list[tuple[str, str, str]], title: str, author: str, body: float) -> dict:
    """Paragraphs -> the book.json model: titles start chapters (level 1 when larger than the other titles),
    note sentinels become links."""
    blocks: list[dict] = []
    chapters: list[dict] = [{"id": "s0", "title": "", "level": 1, "first_block": 0}]
    sizes = {p.size for p in paras if p.kind == "title"}
    two_levels = len({s > 1.1 * body for s in sizes}) == 2
    book_notes: dict[str, str] = {}
    sentinel = {s: n for n, (_, _, s) in enumerate(notes)}
    for p in paras:
        text, links = "", []
        for ch in p.text:
            if ch in sentinel:
                n = sentinel[ch]
                links.append({"pos": len(text), "id": f"n{n + 1}", "m": notes[n][0]})
                book_notes[f"n{n + 1}"] = notes[n][1]
            else:
                text += ch
        for nt in links:  # a marker between two words leaves one space; before punctuation, none
            i = nt["pos"]
            if 0 < i < len(text) and text[i - 1].isalnum() and text[i].isalnum():
                text = text[:i] + " " + text[i:]
                for other in links:
                    if other["pos"] > i:
                        other["pos"] += 1
        text = text.strip()
        if not text:
            continue
        if p.kind == "title":
            chapters.append(
                {
                    "id": f"s{len(chapters)}",
                    "title": text,
                    "level": 1 if not two_levels or p.size > 1.1 * body else 2,
                    "first_block": len(blocks),
                }
            )
        blocks.append(
            {
                "images": [],
                "id": f"b{len(blocks)}",
                "kind": p.kind,
                "chapter": len(chapters) - 1,
                "stanza": None,
                "text": text,
                "em": [],
                "notes": [{**nt, "pos": min(nt["pos"], len(text))} for nt in links],
                "sentences": block_sentences(text, p.kind),
                "audio": True,
            }
        )
    if chapters and not chapters[0]["title"] and (len(chapters) == 1 or chapters[1]["first_block"] == 0):
        chapters = chapters[1:] or chapters
        for b in blocks:
            b["chapter"] = max(0, b["chapter"] - 1)
    return {"title": title, "author": author, "chapters": chapters, "blocks": blocks, "notes": book_notes}


def book_from_lines(pages: list[list[Line]], title: str, author: str) -> dict:
    """Pages of lines (as the text layer gives them) -> book.json."""
    lined = strip_toc(strip_furniture(text_lines(pages)))
    lay = layout(lined)
    lined, notes = split_notes(lined, lay)
    paras = paragraphs(lined, lay, vocabulary(lined))
    return build(paras, notes, title, author, lay.body)


def extract(path: Path) -> dict:
    reader = open_pdf(path)
    pages = page_lines(reader)
    if sum(len(ln.text.split()) for page in pages for ln in page) < 10:
        # a scan yields nothing at all; a short book is still a book
        raise SystemExit("в PDF нет текстового слоя (скан?): распознай его, например в Preview или ABBYY")
    meta = reader.metadata or {}
    title = _meta_text(meta.get("/Title")).strip() or path.stem
    author = _meta_text(meta.get("/Author")).strip()
    return book_from_lines(pages, title, author)


def _meta_text(value: object) -> str:
    """A document info string: pypdf leaves one it cannot read as PDFDocEncoding as bytes, which are read as a
    plain-text file's are (a byte order mark, UTF-8, a Russian code page)."""
    if isinstance(value, bytes):
        from extract_txt import decode

        return decode(bytes(value))
    return str(value or "")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.pdf -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.suffix.lower() == ".pdf"), None)
    if src is None:
        raise SystemExit("no .pdf in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(dump_book(book), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
