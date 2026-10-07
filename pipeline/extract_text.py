"""Extract book text from a fantasy-worlds reader page (or any HTML page) into book.json.

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
import sys
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, CData, Comment, NavigableString, Tag

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_style  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# what one file unpacked from an archive may weigh: a book's text and pictures are far below it, and a
# zip bomb served as a book is refused before it is read
MAX_UNPACKED = 500_000_000


def safe_name(name: str) -> str:
    """A file name taken from downloaded content (an FB2 binary id, an image link), cut to its last part
    so the file lands inside the book's images/; "" when nothing safe is left (`..`, control characters)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if base in ("", ".", "..") or re.search(r"[\x00-\x1f\x7f]", base):
        return ""
    return base


SENT_END = re.compile(r'[.!?…]+[»"”)\]]*')
# a word before "." that does not end a sentence: Russian scholarly and English abbreviations; "т. д.", "т. п.",
# and "etc." end one when a capital follows (_final_abbrev), and so does any of WEAK_ABBREV in the same script
ABBREV = {
    "т", "е", "д", "г", "гг", "стр", "см", "им", "ул", "св", "проф", "др", "пр", "тыс", "млн", "млрд",
    "с", "гл", "в", "вв", "ст", "п", "ч", "н", "к", "изд", "ок", "акад", "англ", "нем", "франц", "фр", "лат",
    "кит", "греч", "санскр", "рис", "табл", "прим", "ред", "пер", "ср", "напр", "сокр", "букв", "вып",
    "e", "g", "i", "mr", "mrs", "dr", "st", "vs", "etc", "p", "pp", "ch", "vol",
}  # fmt: skip
# one letter, also an ordinary word ("Кит. Он плыл") or one that ends a sentence as often ("5 млн. Это много"):
# joined only when no capital of its own script follows ("от англ. Love" stays one)
WEAK_ABBREV = {
    "т", "е", "д", "г", "с", "в", "п", "ч", "н", "к", "e", "g", "i", "p",
    "им", "кит", "ок", "ст", "гл", "изд", "пер", "ред", "нем", "лат", "англ", "франц", "рис", "букв",
    "млн", "млрд", "тыс", "стр", "др", "пр",
}  # fmt: skip
OPENERS = "«\"“'–—-(["  # a sentence may start with one of these
ENDS = "!?…"  # before a single capital: it answers ("Кто там? Я. Ну и что") rather than abbreviates a name
LIST_NUMBER = re.compile(r"[0-9]{1,3}|[IVXLC]{1,6}")
HEADING_KINDS = {"title", "subtitle"}


# elements that hold blocks: inside inline content they part the text with a space
BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dialog", "dir", "div",
    "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "hr", "html", "li", "main", "menu", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul",
})  # fmt: skip
MARK_TAGS = {"em": "em", "i": "em", "strong": "strong", "b": "strong", "sup": "sup", "sub": "sub"}
NOTE_CLOSERS = frozenset(".,;:!?…)]}»”’")  # a note link before one of these takes the space before it along
QUOTES = frozenset("\"'")  # a closer only when it closes: when no letter or digit follows it


class _Flat:
    """inline_text's result as it grows: whitespace collapsed on the way, a mark merged with the one it touches."""

    def __init__(self, cell: bool) -> None:
        self.out: list[str] = []
        self.marks: dict[str, list[list[int]]] = {"em": [], "strong": [], "sup": [], "sub": []}
        self.notes: list[dict] = []
        self.pics: list[dict] = []
        self.cell = cell
        self.lead = 0  # spaces before the first character: a verse line's indentation
        self.start = True
        self.space = False  # whitespace waiting for the next character
        self.breaks = 0  # line breaks waiting for the next character
        self.after_note = False
        self.last = ""
        self.last_marks: frozenset = frozenset()
        self.quote: tuple[int, int] | None = None  # (where, how long) the space before a quote after a note link

    def text(self, s: str, marks: frozenset, pre: bool) -> None:
        for ch in s:
            if ch == "\xa0":
                ch = " "
            if self.start:
                if ch == " ":
                    self.out.append(" ")
                    self.lead += 1
                    continue
                self.start = False
            if ch == "\n" and pre:
                self.br()
            elif ch.isspace():
                self.space = True
            else:
                self.put(ch, marks)

    def br(self) -> None:
        if self.cell:
            self.space = True
        else:
            self.breaks += 1

    def put(self, ch: str, marks: frozenset) -> None:
        if self.quote is not None:
            if self.space or self.breaks or not ch.isalnum():
                self.unquote()  # `сказал {1}" и` -> `сказал" и`; `сказал {1} "Привет"` keeps its space
            self.quote = None
        if len(self.out) > self.lead:
            if self.after_note and ch in NOTE_CLOSERS:
                sep = ""  # `Кит {1}.` -> `Кит.`
            elif self.breaks:
                sep = "\n" * self.breaks
            elif self.space or (self.after_note and self.last.isalnum() and ch.isalnum()):
                sep = " "  # `организме[69]не` -> `организме не`
            else:
                sep = ""
            if sep and self.after_note and ch in QUOTES:
                self.quote = (len(self.out), len(sep))
            both = self.last_marks & marks
            for c in sep:
                self.emit(c, both)
        self.space, self.breaks, self.after_note = False, 0, False
        self.emit(ch, marks)
        self.last, self.last_marks = ch, marks

    def unquote(self) -> None:
        """Take out the space before a quote that turned out to close: as if a closer had followed the note link."""
        w, n = self.quote
        del self.out[w : w + n]

        def at(x: int) -> int:
            return x if x <= w else max(w, x - n)

        for k, rs in self.marks.items():
            out: list[list[int]] = []
            for a, b in ([at(a), at(b)] for a, b in rs):
                if b <= a:
                    continue
                if out and out[-1][1] == a:
                    out[-1][1] = b
                else:
                    out.append([a, b])
            self.marks[k] = out
        for x in (*self.notes, *self.pics):
            x["pos"] = at(x["pos"])

    def finish(self) -> None:
        if self.quote is not None:
            self.unquote()
            self.quote = None

    def emit(self, ch: str, marks: frozenset) -> None:
        pos = len(self.out)
        for m in marks:
            r = self.marks[m]
            if r and r[-1][1] == pos:
                r[-1][1] = pos + 1
            else:
                r.append([pos, pos + 1])
        self.out.append(ch)

    def note(self, nid: str, m: str = "") -> None:
        self.notes.append({"pos": len(self.out), "id": nid, **({"m": m} if m else {})})
        self.after_note = True


def inline_text(el: Tag | None, hooks=None, cell: bool = False, nodes=None) -> dict:
    """Flatten inline content (`el`'s children, or `nodes`): {"text", "em", "strong", "sup", "sub", "notes"}, and
    "pics" when `hooks` is given. Whitespace collapses to one space and is trimmed, except the spaces that open
    the text (a verse line's indentation); `<br>` (and a line end inside `<pre>`) is "\\n", a space in a table
    `cell`; an element holding blocks is a space. A note link drops its text, and the space before it when
    punctuation follows (`Кит {1}.` -> `Кит.`; a straight quote only when no letter or digit follows it, so an
    opening one keeps the space); between two letters it leaves one (`организме[69]не`). Without `hooks` (the
    reader page), `<sup class="note">` with a `data-note-id` link is the page's own note reference.
    `hooks` (the epub walker) has skip(el) -> bool, note_ref(a) -> (id, marker) | None and image(el) -> src | None."""
    f = _Flat(cell)

    def walk(node, marks: frozenset, pre: bool) -> None:
        if isinstance(node, Tag):
            name = node.name
            if hooks is None and name == "sup" and "note" in node.get("class", []):
                a = node.find("a")  # the reader page's own note reference
                nid = a.get("data-note-id") if a else None
                if nid:
                    f.note(nid)
                    return
            if name in ("script", "style", "template") or (hooks is not None and hooks.skip(node)):
                return
            if name == "br":
                f.br()
                return
            if name in ("img", "image", "svg"):
                for im in node.find_all("image") if name == "svg" else [node]:
                    src = hooks.image(im) if hooks is not None else None
                    if src:
                        f.pics.append({"pos": len(f.out), "src": src})
                return
            if name == "a" and hooks is not None:
                ref = hooks.note_ref(node)
                if ref:
                    f.note(*ref)
                    return
            block = name in BLOCK_TAGS
            if block:
                f.space = True
            inner = marks | {MARK_TAGS[name]} if name in MARK_TAGS else marks
            for c in node.children:
                walk(c, inner, pre or name == "pre")
            if block:
                f.space = True
        elif type(node) is NavigableString or isinstance(node, CData):
            f.text(str(node), marks, pre)

    for c in list(el.children) if nodes is None else nodes:
        walk(c, frozenset(), el is not None and el.name == "pre")
    f.finish()
    r = {"text": "".join(f.out), **f.marks, "notes": f.notes}
    if hooks is not None:
        r["pics"] = f.pics
    return r


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


def _is_letter(c: str) -> bool:
    return "А" <= c <= "я" or c in "Ёё" or "A" <= c <= "Z" or "a" <= c <= "z"


def _word_start(text: str, i: int, lo: int) -> int:
    """Where the run of letters that ends at `i` begins (not before `lo`)."""
    while i > lo and _is_letter(text[i - 1]):
        i -= 1
    return i


def _script(c: str) -> str:
    return "lat" if "A" <= c <= "Z" or "a" <= c <= "z" else "cyr" if _is_letter(c) else ""


def _answer(text: str, k: int) -> bool:
    """The capital at `k` opens a sentence of its own: it follows "?", "!" or "…" (`Кто там? Я.`) or a dialogue
    dash, one at the block start or after a sentence end or a colon (`— Кто там? — Я.`)."""
    j = k
    while j > 0 and text[j - 1] in " \n":
        j -= 1
    dash = j > 0 and text[j - 1] in "—–"
    if dash:
        j -= 1
        while j > 0 and text[j - 1] in " \n":
            j -= 1
        if j == 0:
            return True
    while j > 0 and text[j - 1] in '»"”)]':
        j -= 1
    return j > 0 and (text[j - 1] in ENDS or dash and text[j - 1] in ".:")


def _ends_at_letter(text: str, k: int, dot: int, j: int) -> bool:
    """`text[k]`, a capital before the "." at `dot`, ends the sentence before the capital at `j` unless it is an
    initial: one before another (`В. О.`), or one before a surname (a word of two letters or more on the same line)
    that does not answer (`_answer`) and, after a word, is in the letter's script. So `— Я. Ну открывай` and
    `Витамин C. Его` end there, `1. A. Первый` does not."""
    n = len(text)
    if j + 1 < n and text[j + 1] == ".":
        return False
    w = k
    while w > 0 and text[w - 1] in " \n":
        w -= 1
    script = _script(text[j]) == _script(text[k]) or not (w > 0 and _is_letter(text[w - 1]))
    surname = j + 1 < n and _is_letter(text[j + 1]) and script
    return not (surname and "\n" not in text[dot:j] and not _answer(text, k))


def _final_abbrev(text: str, k: int, i: int, lo: int) -> bool:
    """`text[k:i]` is the word before a "." that ends "и т. д.", "и т. п.", "и др.", "и пр." or "etc."."""
    word = text[k:i].lower()
    if word == "etc":
        return True
    if word in ("д", "п"):  # "т." before it
        j = k
        while j > lo and text[j - 1] in " \n":
            j -= 1
        if j < lo + 2 or text[j - 1] != "." or text[j - 2].lower() != "т" or _word_start(text, j - 2, lo) != j - 2:
            return False
        k = j - 2
    elif word not in ("др", "пр"):
        return False
    j = k
    while j > lo and text[j - 1] in " \n":
        j -= 1
    return j < k and j > lo and text[j - 1].lower() == "и" and _word_start(text, j - 1, lo) == j - 1


def split_sentences(text: str) -> list[list[int]]:
    """Return [start,end] char ranges of sentences in text (Russian-aware heuristic). A sentence does not end
    at an abbreviation (ABBREV) or an initial (`Н. Жирардо`, `Ю.Щ.`, `_ends_at_letter`); `и т. д.`, `Кит.` and the
    like end one before a capital; a list number opening the block (`1.`, `IV.`) belongs to the sentence after
    it. "\\n" counts as a space."""
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
        while j < n and text[j] in " \n":
            j += 1
        if end >= n:
            out.append([start, n])
            start = n
            break
        next_ch = text[j] if j < n else ""
        is_abbrev = False
        if text[m.start()] == ".":
            k = _word_start(text, m.start(), start)
            word = text[k : m.start()]
            if len(word) == 1 and word.isupper():
                is_abbrev = not (next_ch.isupper() and _ends_at_letter(text, k, m.start(), j))  # an initial
            elif word.lower() in WEAK_ABBREV:
                is_abbrev = not next_ch.isupper() or _script(next_ch) != _script(word[0])
            elif word.lower() in ABBREV:
                is_abbrev = not (next_ch.isupper() and _final_abbrev(text, k, m.start(), start))
            elif not out and m.end() == m.start() + 1 and LIST_NUMBER.fullmatch(text[start : m.start()].strip()):
                is_abbrev = True  # a list number opening the block
        boundary = j > end and not is_abbrev and (next_ch.isupper() or next_ch in OPENERS or next_ch.isdigit())
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
        while a < b and text[a] in " \n":
            a += 1
        while b > a and text[b - 1] in " \n":
            b -= 1
        if b > a:
            cleaned.append([a, b])
    return cleaned


def block_sentences(text: str, kind: str) -> list[list[int]]:
    """A block's sentences: a heading is one, every other kind is split."""
    return [[0, len(text)]] if kind in HEADING_KINDS else split_sentences(text)


BLOCK_KEYS = (
    "images", "id", "kind", "chapter", "stanza", "text", "em", "strong", "sup", "sub", "notes", "sentences", "audio",
    "pics", "rows", "st",
)  # fmt: skip
BLOCK_OPTIONAL = {"strong", "sup", "sub", "pics", "rows", "st"}
NOTE_KEYS = ("text", "em", "strong", "sup", "sub", "pics", "kinds")


def _ordered(d: dict, keys: tuple[str, ...], optional) -> dict:
    """`d` with `keys` first in that order, empty optional ones dropped, anything else after in its own order."""
    out = {k: d[k] for k in keys if k in d and not (k in optional and not d[k])}
    out.update((k, v) for k, v in d.items() if k not in keys)
    return out


def _note(v):
    """A book note: a string, or an object when it has markup, pictures or more than one paragraph."""
    if not isinstance(v, dict):
        return v
    o = _ordered(v, NOTE_KEYS, NOTE_KEYS[1:])
    return o["text"] if list(o) == ["text"] and "\n" not in o["text"] else o


def dump_book(book: dict) -> str:
    """book.json text: block keys in the contract's order, empty optional keys left out."""
    blocks = []
    for b in book["blocks"]:
        b = _ordered(b, BLOCK_KEYS, BLOCK_OPTIONAL)
        if "images" in b:
            b["images"] = [
                _ordered(im, ("src", "w", "h", "after"), {"after"}) if isinstance(im, dict) else im
                for im in b["images"]
            ]
        if "notes" in b:
            b["notes"] = [_ordered(nt, ("pos", "id", "m"), {"m"}) for nt in b["notes"]]
        blocks.append(b)
    out = {k: blocks if k == "blocks" else v for k, v in book.items()}
    if isinstance(out.get("notes"), dict):
        out["notes"] = {k: _note(v) for k, v in out["notes"].items()}
    return json.dumps(out, ensure_ascii=False)


# ---------------- HTML: fantasy-worlds reader pages and any other page ----------------

MARKS = ("em", "strong", "sup", "sub")
CLOSING = NOTE_CLOSERS  # a note link before one of these (or a quote that closes) takes the space before it along
TITLE_END = ".!?:;…—-,"  # the contract's list and a comma: `Глава первая,` + `в которой`
HEADS = ("h1", "h2", "h3", "h4", "h5", "h6")
HTML_BLOCK = {
    "address", "article", "aside", "blockquote", "caption", "center", "dd", "details", "dialog", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "header", "hgroup", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", *HEADS,
}  # fmt: skip
NOTE_KINDS = {"author": "text-author"}  # a block kind as a note paragraph's kind, where the names differ
NOTEREF_CLASS = re.compile(r"note|\bfn", re.I)


def _marker(s: str) -> str:
    """A note link's visible marker without its brackets: `{721}` -> `721`, `[1]` -> `1`."""
    return s.strip().strip("[]{}()").strip()


def _edit(r: dict, a: int, b: int, s: str) -> None:
    """Replace r["text"][a:b] with `s` and move every mark, note and picture of `r` with the text: a mark that
    starts at an insertion starts after it, anything that ends or sits there stays."""
    d = len(s) - (b - a)

    def start(x: int) -> int:
        return x if x < a else a if x < b else x + d

    def end(x: int) -> int:
        return x if x <= a else a if x < b else x + d

    r["text"] = r["text"][:a] + s + r["text"][b:]
    for k in MARKS:
        if r.get(k):
            r[k] = [[start(g[0]), end(g[1]), *g[2:]] for g in r[k] if end(g[1]) > start(g[0])]
    for k in ("notes", "pics"):
        if r.get(k):
            r[k] = [{**n, "pos": end(n["pos"])} for n in r[k]]


def _inline(el: Tag, kind: str) -> dict:
    """inline_text of an HTML block as {"text", "em", "notes", and "strong"/"sup"/"sub"/"pics" when it gives them},
    with each note's marker `m`, the space around a removed note link set right (`Кит {1}.` -> `Кит.`,
    `организме[69]не` -> `организме не`, `Ещё {2} слово` -> the note after `Ещё`) and leading indentation kept
    only for verse."""
    res = inline_text(el)
    r = dict(res) if isinstance(res, dict) else {"text": res[0], "em": res[1], "notes": res[2]}
    r["notes"] = [dict(n) for n in r.get("notes") or []]
    refs = [s for s in el.find_all("sup", class_="note") if s.find("a") and s.find("a").get("data-note-id")]
    if len(refs) == len(r["notes"]):
        for n, s in zip(r["notes"], refs, strict=True):
            m = _marker(s.get_text())
            if m and "m" not in n:
                n["m"] = m
    for i in sorted(range(len(r["notes"])), key=lambda i: -r["notes"][i]["pos"]):
        p, t = r["notes"][i]["pos"], r["text"]
        closes = p == len(t) or t[p] in CLOSING or (t[p] in QUOTES and (p + 1 == len(t) or not t[p + 1].isalnum()))
        if 0 < p <= len(t) and t[p - 1] == " " and closes:
            _edit(r, p - 1, p, "")
        elif 0 < p < len(t) and t[p - 1] == " ":
            r["notes"][i]["pos"] = p - 1  # `Ещё {2} слово`: the marker goes with the word before it
        elif 0 < p < len(t) and t[p - 1].isalnum() and t[p].isalnum():
            _edit(r, p, p, " ")
    lead = len(r["text"]) - len(r["text"].lstrip())
    if lead and kind != "verse":
        _edit(r, 0, lead, "")
    return r


def _html_block(r: dict, bid: str, kind: str, chapter: int, stanza, audio: bool, images: list) -> dict:
    b = {"images": images, "id": bid, "kind": kind, "chapter": chapter, "stanza": stanza, "text": r["text"]}
    b["em"] = r.get("em") or []
    for k in MARKS[1:]:
        if r.get(k):
            b[k] = r[k]
    b["notes"] = r["notes"]
    b["sentences"] = block_sentences(r["text"], kind)
    b["audio"] = audio
    if r.get("pics"):
        b["pics"] = r["pics"]
    return b


def _join_title(parts: list[str]) -> str:
    """A chapter title from several title paragraphs: `". "` between them, `" "` after one that ends in punctuation."""
    out = ""
    for p in parts:
        if p:
            out = p if not out else out + (" " if out[-1] in TITLE_END else ". ") + p
    return re.sub(r"\s+", " ", out).strip()


def _is_block(t: Tag) -> bool:
    return t.name in HTML_BLOCK or (t.name == "cite" and t.find("p") is not None)


def _wrap(nodes: list) -> Tag:
    """One element holding a run of inline nodes (the element itself when the run is one)."""
    if len(nodes) == 1 and isinstance(nodes[0], Tag):
        return nodes[0]
    w = Tag(name="span")
    for n in nodes:
        w.append(n.extract())
    return w


def _layout_table(t: Tag) -> bool:
    """A table that lays a page out (its cells hold paragraphs, lists or tables) rather than holding data."""
    return t.find(["table", "p", "div", "blockquote", "ul", "ol", "pre", *HEADS]) is not None


def _walk_html(el: Tag, emit, kind: str = "p", stanza=None, tables: bool = True) -> None:
    """Every block under `el` in order, as emit(kind, nodes, stanza): a heading ("h1".."h6"), "gap" (an empty
    line), "table" (a data table), or a paragraph of `kind` made of `nodes`: an element, or a run of text and
    inline elements sitting between blocks (text in a div, li or td). Poems, stanzas, epigraphs, quotes and
    text-authors are known by the reader page's classes and the HTML tags."""
    run: list = []

    def flush():
        if run:
            emit(kind, run.copy(), stanza)
            run.clear()

    for c in list(el.children):
        if not isinstance(c, Tag):
            if type(c) is NavigableString:  # comments, doctypes and the like are no text
                run.append(c)
            continue
        if not _is_block(c):
            run.append(c)
            continue
        flush()
        cls, name = c.get("class", []), c.name
        if name in HEADS:
            emit(name, [c], stanza)
        elif "empty-line" in cls:
            emit("gap", [], stanza)
        elif name == "table" and tables and not _layout_table(c):
            emit("table", [c], stanza)
        elif "stanza" in cls:
            _walk_html(c, emit, "verse", c, tables)
        elif "poem" in cls:  # lines straight in a poem are one stanza
            _walk_html(c, emit, "verse", c if stanza is None else stanza, tables)
        elif "epigraph" in cls:
            _walk_html(c, emit, "epigraph", stanza, tables)
        elif name in ("blockquote", "cite"):
            _walk_html(c, emit, "cite", stanza, tables)
        elif name != "hr":
            k = "author" if "text-author" in cls else "date" if "date" in cls else "verse" if "verse" in cls else kind
            if name == "p" and c.find(_is_block) is None:
                emit(k, [c], stanza)
            else:
                _walk_html(c, emit, k, stanza, tables)
    flush()


def _note_value(body: Tag, image) -> str | dict:
    """A note's text: a string, or the contract's object when it has marks, pictures or more than one paragraph:
    paragraphs joined by a blank line, the lines of a stanza by a line break, `kinds` for the paragraphs that are
    not plain. Only one level: links inside a note are not notes."""
    o: dict = {"text": "", **{k: [] for k in MARKS}, "pics": [], "kinds": []}
    last = [None]  # the stanza of the last paragraph

    def emit(kind: str, nodes: list, stanza) -> None:
        if kind == "gap":
            return
        el = _wrap(nodes)
        kind = "subtitle" if kind in HEADS else kind
        r = _inline(el, kind)
        t = r["text"]
        if t.strip():
            sep = "" if not o["text"] else "\n" if stanza is not None and stanza is last[0] else "\n\n"
            base = len(o["text"]) + len(sep)
            o["text"] += sep + t
            for k in MARKS:
                o[k] += [[a + base, b + base] for a, b in r.get(k) or []]
            o["pics"] += [{**p, "pos": p["pos"] + base} for p in r.get("pics") or []]
            if kind != "p":
                o["kinds"].append([base, base + len(t), NOTE_KINDS.get(kind, kind)])
            last[0] = stanza
        if not t.strip() or "pics" not in r:
            for img in [el] if el.name == "img" else el.find_all("img"):
                e = image(img)
                if e:
                    o["pics"].append({"pos": len(o["text"]), "src": e["src"]})

    _walk_html(body, emit, tables=False)
    o = {k: v for k, v in o.items() if v or k == "text"}
    return o["text"] if list(o) == ["text"] and "\n" not in o["text"] else o


def _note_links(links: list[Tag], nid_of) -> None:
    """Make in-page links to notes (`<a href="#c_6"><sup>{6}</sup></a>`) the reader page's own note reference,
    `<sup class="note"><a data-note-id>`, which inline_text takes for a note; the marker stays its text."""
    for a in links:
        nid = nid_of(a)
        if nid is None:
            continue
        outer = a
        if a.parent is not None and a.parent.name == "sup" and a.parent.get_text().strip() == a.get_text().strip():
            outer = a.parent
        sup = Tag(name="sup", attrs={"class": ["note"]})
        link = Tag(name="a", attrs={"data-note-id": nid})
        link.string = a.get_text()
        sup.append(link)
        outer.replace_with(sup)


BACK_MARKS = ("↩", "↩︎", "^", "↑")


def _back_link(a: Tag) -> bool:
    """A link from a note back to the text: by its role or its arrow."""
    return "backlink" in a.get("role", "") or a.get_text(strip=True) in BACK_MARKS


def _has_sup(t: Tag) -> bool:
    return t.name == "sup" or t.find("sup") is not None or t.find_parent("sup") is not None


def _generic_notes(soup: BeautifulSoup, image) -> dict:
    """Footnotes of any page: a short marker link (in or around a `sup`, or a noteref by role or class) to an
    element of the page that does not hold the link. The element becomes the note and leaves the text, with
    its back links and any container it leaves empty. A target that is itself a back link to the reference
    (`<a id="fn1" href="#ref1">`) stands for the paragraph it opens. A note may hold another note's link (its
    marker goes); a target that is only an anchor inside a paragraph holding note links is running text."""
    ids = {t["id"]: t for t in soup.find_all(id=True)}
    found: list[tuple[Tag, str]] = []  # links by identity: two links with the same markup are two links
    bodies: dict[str, Tag] = {}
    anchors: set[str] = set()  # notes whose target is an element inside the body, not the body itself
    heads: dict[int, bool] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        tgt = ids.get(unquote(href[1:])) if href.startswith("#") else None
        if tgt is None or any(p is tgt for p in a.parents) or _back_link(a):
            continue
        if tgt.name == "sup" or tgt.find_parent("sup"):
            continue  # a back link from a note to the reference in its sup
        if tgt.name == "a" and tgt.get("href"):
            # a link to a link: the note's own back link (`<a id="fn1" href="#ref1">1</a> Текст`), not the reference
            back = tgt["href"]
            if not back.startswith("#") or _has_sup(tgt):
                continue
            back_id = unquote(back[1:])
            if back_id not in [t.get("id") for t in (a, a.parent) if t is not None]:
                continue
        sup = a.parent if a.parent is not None and a.parent.name == "sup" else None
        how = " ".join([a.get("role", ""), a.get("epub:type", ""), *a.get("class", []), *(sup or a).get("class", [])])
        if not (sup or a.find("sup") or "noteref" in how or NOTEREF_CLASS.search(how)):
            continue
        if not 0 < len(_marker(a.get_text())) <= 5:
            continue
        body = tgt
        while not _is_block(body) and body.parent is not None and body.parent.name not in ("body", "[document]"):
            body = body.parent
        if id(body) not in heads:
            heads[id(body)] = body.name in HEADS or body.find(HEADS) is not None
        if heads[id(body)]:
            continue  # a link to a heading or a section is a cross-reference
        found.append((a, tgt["id"]))
        bodies[tgt["id"]] = body
        if body is not tgt and not (tgt.name == "a" and tgt.get("href")):
            anchors.add(tgt["id"])
    # an anchor in the running text: its paragraph holds a note link
    holders = {id(p) for a, _ in found for p in a.parents}
    bad = {nid for nid in anchors if id(bodies[nid]) in holders}
    found = [(a, nid) for a, nid in found if nid not in bad]
    bodies = {nid: body for nid, body in bodies.items() if nid not in bad}
    ref_ids = {t["id"] for a, _ in found for t in (a, a.parent) if t is not None and t.get("id")}
    for body in bodies.values():
        for b in body.find_all("a", href=True):
            back = b["href"].startswith("#") and unquote(b["href"][1:]) in ref_ids
            if back or _back_link(b):
                b.decompose()
    nid_of = {id(a): nid for a, nid in found}
    _note_links([a for a, _ in found], lambda a: nid_of.get(id(a)))
    notes = {nid: _note_value(body, image) for nid, body in bodies.items()}
    for body in bodies.values():
        if body.decomposed:
            continue  # inside another note, gone with it
        parent = body.parent
        body.decompose()
        while parent is not None and parent.name not in ("body", "[document]") and not parent.get_text(strip=True):
            if parent.find("img"):
                break
            up = parent.parent
            parent.decompose()
            parent = up
    return notes


def _trimmed(text: str, a: int, b: int) -> list[int]:
    """[a, b] without the tabs and spaces at its ends: a row's sentence when its first or last cell is empty."""
    while a < b and text[a] in "\t ":
        a += 1
    while b > a and text[b - 1] in "\t ":
        b -= 1
    return [a, b]


def _table(t: Tag) -> tuple[dict, list] | None:
    """A data table as one block's text: the cells of a row joined by a tab, the rows by a line break; `rows`
    holds each cell's range, a header cell's with a third element 1. Rows without text are left out."""
    r: dict = {"text": "", **{k: [] for k in MARKS}, "notes": [], "pics": []}
    rows: list = []
    for tr in t.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        got = [_inline(c, "table") for c in cells]
        if not any(x["text"].strip() for x in got):
            continue
        if rows:
            r["text"] += "\n"
        row = []
        for i, (c, x) in enumerate(zip(cells, got, strict=True)):
            if i:
                r["text"] += "\t"
            base = len(r["text"])
            r["text"] += x["text"].replace("\n", " ").replace("\t", " ")
            for k in MARKS:
                r[k] += [[a + base, b + base] for a, b in x.get(k) or []]
            for k in ("notes", "pics"):
                r[k] += [{**n, "pos": n["pos"] + base} for n in x.get(k) or []]
            row.append([base, len(r["text"]), 1] if c.name == "th" else [base, len(r["text"])])
        rows.append(row)
    return (r, rows) if rows else None


def _size(img: Tag) -> dict:
    """{"w", "h"} from an image's width and height when both are whole numbers (`100%` is not one)."""
    w, h = img.get("width") or "", img.get("height") or ""
    return {"w": int(w), "h": int(h)} if re.fullmatch(r"[0-9]+", w) and re.fullmatch(r"[0-9]+", h) else {}


def _container(soup: BeautifulSoup) -> Tag:
    """The smallest element holding most (four fifths) of the page's paragraph text: the article, not a box
    beside it, and the body when the text sits in it directly or in several chapters side by side."""
    root = soup.body or soup
    kinds = {"div", "article", "section", "main", "td"}
    sizes: dict[int, int] = {}
    total = 0
    for p in root.find_all("p"):
        if p.find_parent("p") is not None:
            continue  # its text is counted with the paragraph around it
        n = len(p.get_text())
        total += n
        for up in p.parents:
            if up is root:
                break
            if up.name in kinds:
                sizes[id(up)] = sizes.get(id(up), 0) + n
    best = root
    for cand in root.find_all(list(kinds)):  # outer before inner: the last one that qualifies is the smallest
        if 5 * sizes.get(id(cand), 0) >= 4 * total > 0 and any(p is best for p in cand.parents):
            best = cand
    return best


def _page_chrome(t: Tag) -> bool:
    """A part of the page around the text: navigation, a sidebar, a form, or a header outside the article."""
    if t.name != "header":
        return True
    return t.find_parent(["article", "main"]) is None or t.find("nav") is not None


def extract_generic(soup: BeautifulSoup, src: Path) -> dict:
    """Any HTML page: the container holding most paragraph text, walked block by block (headings, paragraphs,
    quotes, list items, text in divs and table cells); a data table is a "table" block, a footnote a note, and a
    picture is kept when it was saved next to the page (images/<name>)."""

    def image(img: Tag) -> dict | None:
        s = img.get("src") or img.get("data-src") or ""
        name = safe_name(unquote(s.split("#")[0].split("?")[0]))
        if not name or s.startswith("data:") or not (src.parent / "images" / name).is_file():
            return None
        return {"src": "images/" + name, **_size(img)}

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    notes = _generic_notes(soup, image)
    chrome = soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"])
    for t in [t for t in chrome if _page_chrome(t)]:
        if not t.decomposed:
            t.decompose()
    best = _container(soup)
    blocks: list[dict] = []
    chapters: list[dict] = [{"id": "s0", "title": "", "level": 1, "first_block": 0}]
    pending: list[dict] = []  # pictures since the last block, for the next one

    def add(r: dict, kind: str, bid: str | None, audio: bool = True) -> dict | None:
        if not r["text"].strip():
            return None
        blocks.append(_html_block(r, bid or f"b{len(blocks)}", kind, len(chapters) - 1, None, audio, pending.copy()))
        pending.clear()
        return blocks[-1]

    def trail() -> None:
        """Pictures left at a chapter's end go after its last block."""
        if pending and blocks:
            blocks[-1]["images"] = blocks[-1]["images"] + [{**im, "after": True} for im in pending]
            pending.clear()

    def emit(kind: str, nodes: list, stanza) -> None:
        if kind == "gap":
            return
        if kind == "table":
            got = _table(nodes[0])
            if got:
                r, rows = got
                b = add(r, "table", nodes[0].get("id"), audio=False)
                if b:
                    b["sentences"] = [_trimmed(r["text"], row[0][0], row[-1][1]) for row in rows]
                    b["rows"] = rows
            return
        el = _wrap(nodes)
        if kind in ("h1", "h2", "h3"):
            r = _inline(el, "title")
            if r["text"]:
                trail()
                title = " ".join(r["text"].split())
                chapters.append({"id": f"s{len(chapters)}", "title": title, "level": 2, "first_block": len(blocks)})
                add(r, "title", el.get("id"))
            return
        kind = "subtitle" if kind in HEADS else kind
        r = _inline(el, kind)
        if not r["text"].strip() or "pics" not in r:
            pending.extend(e for e in map(image, [el] if el.name == "img" else el.find_all("img")) if e)
        add(r, kind, el.get("id"))

    _walk_html(best, emit)
    trail()
    if chapters[0]["title"] == "" and len(chapters) > 1 and chapters[1]["first_block"] == 0:
        chapters.pop(0)
        for b in blocks:
            b["chapter"] = max(0, b["chapter"] - 1)
    title = soup.title.get_text(strip=True) if soup.title else src.stem
    return {"title": title, "author": "", "chapters": chapters, "blocks": blocks, "notes": notes}


def extract(src: Path) -> dict:
    soup = BeautifulSoup(src.read_text(encoding="utf-8", errors="replace"), "html.parser")
    article = soup.find("article", id="book-content")
    if article is None:
        return extract_generic(soup, src)
    blocks: list[dict] = []
    chapters: list[dict] = []
    notes: dict[str, str | dict] = {}
    stanza_counter = [0]
    pending_images: list[dict] = []  # illustrations seen since the last block; attached to the next block
    gap = [0]  # empty lines since the last block

    def image(img: Tag | None) -> dict | None:
        src = (img.get("data-src") or img.get("src") or "") if img else ""
        if not src or src.startswith("cover"):
            return None
        return {"src": "images/" + src.rsplit("/", 1)[-1], **_size(img)}

    def add_block(el: Tag, kind: str, chapter: int, audio: bool = True, stanza=None, bid=None):
        r = _inline(el, kind)
        if not r["text"].strip():
            return
        bid = bid or el.get("id") or f"b{len(blocks)}"
        blocks.append(_html_block(r, bid, kind, chapter, stanza, audio, pending_images.copy()))
        st = extract_style.block_style(kind, {}, {}, gap[0])
        if st:
            blocks[-1]["st"] = st
        gap[0] = 0
        pending_images.clear()

    def trail() -> None:
        """Illustrations left at a chapter's end go after its last block."""
        if pending_images and blocks:
            blocks[-1]["images"] = blocks[-1]["images"] + [{**im, "after": True} for im in pending_images]
            pending_images.clear()

    def walk_section(sec: Tag, level: int):
        trail()
        gap[0] = 0  # a blank line before a chapter is not the chapter's
        h2 = sec.find("h2", recursive=False)
        parts = [_inline(p, "title")["text"] for p in (h2.find_all("p") or [h2])] if h2 else []
        title = _join_title(parts)
        if title == "<title unassigned>":
            title = "* * *"
        ch_idx = len(chapters)
        chapters.append({"id": sec.get("id"), "title": title, "level": level, "first_block": len(blocks)})
        if h2 is not None:
            add_block(h2, "title", ch_idx, bid=f"t-{sec.get('id')}")
        for child in sec.children:
            if isinstance(child, Tag) and child is not h2:
                handle(child, ch_idx, level)

    def handle(el: Tag, ch_idx: int, level: int, kind_override: str | None = None, audio: bool = True, stanza=None):
        name = el.name
        cls = el.get("class", [])
        if name == "section":
            walk_section(el, level + 1)
        elif name in HEADS or "title" in cls or "subtitle" in cls:
            # a heading inside the text: a cite's «Мема 1», a poem's or a stanza's title
            add_block(el, "subtitle", ch_idx, audio=audio, bid=f"h3-{len(blocks)}" if name == "h3" else None)
        elif name == "p":
            if "text-author" in cls:
                kind = "author"
            elif "date" in cls and kind_override == "verse":
                kind = "date"
            else:
                kind = kind_override or ("verse" if "verse" in cls else "p")
            add_block(el, kind, ch_idx, audio=audio, stanza=stanza)
        elif name == "div":
            if "poem" in cls or "stanza" in cls:
                if "stanza" in cls:
                    stanza_counter[0] += 1
                    stanza = stanza_counter[0]
                for c in el.children:
                    if isinstance(c, Tag):
                        handle(c, ch_idx, level, "verse", audio, stanza)
            elif "epigraph" in cls:
                for c in el.children:
                    if isinstance(c, Tag):
                        handle(c, ch_idx, level, "epigraph", audio)
            elif el.get("id") == "annotation":
                for p in el.find_all("p", recursive=False):
                    handle(p, ch_idx, level, kind_override="annotation", audio=False)
            elif "img-wrap" in cls:
                entry = image(el.find("img"))
                if entry:
                    pending_images.append(entry)
                return
            elif "empty-line" in cls:
                gap[0] += 1
            else:
                for c in el.children:
                    if isinstance(c, Tag):
                        handle(c, ch_idx, level, kind_override, audio, stanza)
        elif name == "cite":
            for c in el.children:
                if isinstance(c, Tag):
                    handle(c, ch_idx, level, "cite", audio)
        elif name in ("script", "img", "svg"):
            return
        else:
            for c in el.children:
                if isinstance(c, Tag):
                    handle(c, ch_idx, level, kind_override, audio, stanza)

    # a note is a div.note (id "note-<id>") of the notes section; a plain link to it or into it is a note link
    targets: dict[str, str] = {}
    for sec in article.find_all("section", class_="notes", recursive=False):
        for note in sec.find_all("div", class_="note"):
            nid = note.get("id", "").replace("note-", "")
            if not nid:
                continue
            targets[nid] = targets[note["id"]] = nid
            for t in note.find_all(id=True):
                targets.setdefault(t["id"], nid)
    links = [
        a
        for a in article.find_all("a", href=True)
        if a.find_parent("sup", class_="note") is None and a.find_parent("section", class_="notes") is None
    ]
    _note_links(links, lambda a: targets.get(unquote(a["href"][1:])) if a["href"].startswith("#") else None)

    for sec in article.find_all("section", recursive=False):
        cls = sec.get("class", [])
        if "notes" in cls:
            for note in sec.find_all("div", class_="note"):
                nid = note.get("id", "").replace("note-", "")
                body = note.find("div", class_="note__body")
                notes[nid] = _note_value(body, image) if body else ""
            continue
        walk_section(sec, 1)
    trail()

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
    (args.book_dir / "book.json").write_text(dump_book(book), encoding="utf-8")
    n_audio = sum(1 for b in book["blocks"] if b["audio"])
    n_sent = sum(len(b["sentences"]) for b in book["blocks"])
    n_words = sum(len(b["text"].split()) for b in book["blocks"] if b["audio"])
    print(
        f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} audio_blocks={n_audio} "
        f"sentences={n_sent} words={n_words} notes={len(book['notes'])}"
    )


if __name__ == "__main__":
    main()
