"""Extract a plain-text book into the book.json model.

Encoding: a byte order mark decides (UTF-8, UTF-16, UTF-32); else zero bytes on one side of each pair are UTF-16
without one; else the text is UTF-8 when at least 99% of its bytes decode as such (the rest replaced); else it is
whichever of cp1251, koi8-r, cp866 and mac-cyrillic reads most like Russian (letter frequencies and
letter case, not the first that decodes), and latin-1 when none of them reads as Russian at all.

Paragraphs: a blank line ends one. In a hard-wrapped text (most lines about the same width) so does an indented
line and a short line that ends a sentence, and a word hyphenated across two lines is joined again (the hyphen
stays where the PDF rule keeps it: чем-то, кое-как, a word the text spells with it). A text with long lines is one
line per paragraph.

Chapters: a line that stands alone (blank lines around it, or in a text without blank lines between paragraphs,
a paragraph of its own) and looks like a heading: Глава/Часть/Книга/Том/Chapter/Part/Book and a number, a lone
number, an all-caps line (unless the whole text is in capitals); Пролог/Эпилог/Предисловие/Послесловие alone or
with a short rest, not a sentence that starts with the word. `* * *`, `***`, `---` are scene breaks: not text, a
gap before the next block. A `*` glued to the end of a word (not the closing one of a `*word*` pair) and a
paragraph within the next three that starts with `*` are a note and its body.

ios/Sources/Import/TXT.swift is the same code on the phone and must stay byte for byte equal.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_style  # noqa: E402
from extract_pdf import Line, keeps_hyphen, vocabulary  # noqa: E402
from extract_text import block_sentences, dump_book  # noqa: E402

# ---------------------------------------------------------------- encoding

BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
CYRILLIC = ("cp1251", "koi8_r", "cp866", "mac_cyrillic")  # in this order on a tie
SAMPLE = 1 << 18  # bytes scored: enough to tell the encodings apart, cheap on a big book
# Russian letters per thousand: the right encoding puts the frequent letters where the text has the frequent bytes
FREQ = {
    "о": 110, "е": 85, "а": 80, "и": 74, "н": 67, "т": 63, "с": 55, "р": 47, "в": 45, "л": 44, "к": 35, "м": 32,
    "д": 30, "п": 28, "у": 26, "я": 20, "ы": 19, "ь": 17, "г": 17, "з": 17, "б": 16, "ч": 14, "й": 12, "х": 10,
    "ж": 9, "ш": 7, "ю": 6, "ц": 5, "щ": 4, "э": 3, "ф": 3, "ъ": 1, "ё": 1,
}  # fmt: skip
NEUTRAL = set("«»—–…„“”‘’№©°·•§\xa0\xad")  # punctuation a Russian text has: no evidence either way
PENALTY = 30  # a sign, a box-drawing piece, a letter glued to a Latin one, a case change inside a word
UTF8_SHARE = 99  # per cent of the bytes that must be UTF-8 for a text with a few broken bytes to be read as UTF-8
BAD_BYTE = re.compile("[\udc80-\udcff]")  # a byte that is not UTF-8, as surrogateescape keeps it


def _ru_lower(c: str) -> bool:
    return "а" <= c <= "я" or c == "ё"


def _ru_upper(c: str) -> bool:
    return "А" <= c <= "Я" or c == "Ё"


def _latin(c: str) -> bool:
    return "a" <= c <= "z" or "A" <= c <= "Z"


def russian_score(s: str) -> int:
    """How much `s` reads like Russian: each letter of a Russian word counts as often as the letter is in Russian,
    while signs, letters inside Latin words and a change of case inside a word (a capital after a small letter,
    a small letter after two capitals) count against it. ASCII and a lone letter say nothing."""
    score = 0
    n = len(s)
    for i, c in enumerate(s):
        if c < "\x80":
            continue
        if not _ru_lower(c) and not _ru_upper(c):
            if c not in NEUTRAL:
                score -= PENALTY
            continue
        a = s[i - 1] if i > 0 else " "
        b = s[i + 1] if i + 1 < n else " "
        if (
            _latin(a)
            or _latin(b)
            or (_ru_upper(c) and _ru_lower(a))
            or (_ru_lower(c) and _ru_upper(a) and i > 1 and _ru_upper(s[i - 2]))
        ):
            score -= PENALTY
        elif _ru_lower(a) or _ru_upper(a) or _ru_lower(b) or _ru_upper(b):
            score += FREQ[c.lower()]
    return score


def bomless_utf16(raw: bytes) -> str | None:
    """UTF-16 without a byte order mark: the zero high bytes of its spaces, digits and Latin letters all fall on
    one side of each byte pair (the odd ones in little-endian), never in a text of one byte per character."""
    n = min(len(raw), SAMPLE) // 2 * 2
    even, odd = raw[0:n:2].count(0), raw[1:n:2].count(0)
    if odd and odd * 20 >= n // 2 and even * 10 <= odd:
        return "utf-16-le"
    if even and even * 20 >= n // 2 and odd * 10 <= even:
        return "utf-16-be"
    return None


def decode(raw: bytes) -> str:
    for bom, enc in BOMS:
        if raw.startswith(bom):
            return raw[len(bom) :].decode(enc, "replace")
    if enc := bomless_utf16(raw):
        return raw.decode(enc, "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    bad = len(BAD_BYTE.findall(raw.decode("utf-8", "surrogateescape")))
    if (len(raw) - bad) * 100 >= UTF8_SHARE * len(raw):  # a stray byte does not make a UTF-8 book a code page one
        return raw.decode("utf-8", "replace")
    sample = raw[:SAMPLE]
    best, top = "latin-1", 0
    for enc in CYRILLIC:
        score = russian_score(sample.decode(enc, "replace"))
        if score > top:
            best, top = enc, score
    return raw.decode(best, "replace")


def read_text(path: Path) -> str:
    return decode(path.read_bytes())


# ---------------------------------------------------------------- lines

ORDINAL = (
    "перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|[а-яё]+надцат|двадцат|тридцат|сороков|последн"
    "|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|(?:thir|four|fif|six|seven|eigh|nine)teen"
    "|twenty|thirty|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last"
)
END = r"(?![0-9A-Za-zА-яЁё])"  # a word ends here: the same characters in Python's re and the phone's ICU, unlike \b
KEYWORD = re.compile(
    r"^(?:(?:глава|часть|книга|том|chapter|part|book) (?:[0-9]{1,3}|(?-i:[IVXLCDM]{1,7})|(?:"
    + ORDINAL
    + r")[а-яёa-z]*)"
    + END
    + r"|(?:пролог|эпилог|предисловие|послесловие)"
    + END
    + r").{0,60}$",
    re.I,
)
STRONG = re.compile(
    r"^(?:глава|часть|книга|chapter|part|book) (?:[0-9]{1,3}|(?-i:[IVXLCDM]{1,7})|(?:"
    + ORDINAL
    + r")[а-яёa-z]*)"
    + END
    + r".{0,60}$",
    re.I,
)
PROLOGUE = re.compile(r"^(?:пролог|эпилог|предисловие|послесловие)" + END, re.I)
SENTENCE = re.compile(r"[.!?…][»”\"')\]]*$")  # the line ends a sentence
NUMBER = re.compile(r"^(?:[0-9]{1,3}|[IVXLCDM]{1,7})\.?$")
CAPS = re.compile(r"^[А-ЯЁA-Z][А-ЯЁA-Z0-9 .,:;!?«»\"'()\-–—…]{1,79}$")
CAPS_WORD = re.compile(r"[А-ЯЁA-Z]{2}")
BIG_PART = re.compile(r"^(?:часть|книга|том|part|book)" + END, re.I)
SCENE_BREAK = re.compile(r"^(?:(?:\* ?){3,}|(?:[-–—] ?){3,})$")
ENDS_SENTENCE = re.compile(r"[.!?…:][»”\"')\]]*$")
SPACE = re.compile(r"\s+")
BROKEN = re.compile(r"([^\W\d_]+(?:-[^\W\d_]+)*)-$")  # the word before a hyphen at the line end
LEADING = re.compile(r"[^\W\d_]+")


def heading_line(s: str, caps: bool) -> bool:
    """A short line that names a chapter: a keyword and a number, a lone number, or capitals (when `caps`).
    Dialogue never is, nor a line that runs on with a comma, nor a sentence of five words or more that starts
    with Пролог/Эпилог/Предисловие/Послесловие."""
    words = len(s.split())
    if len(s) > 80 or words > 8 or s.endswith((",", ";")) or s.startswith(("-", "–", "—")):
        return False
    if KEYWORD.match(s) and not (PROLOGUE.match(s) and words > 4 and SENTENCE.search(s)):
        return True
    if NUMBER.match(s):
        return True
    return caps and bool(CAPS.match(s)) and bool(CAPS_WORD.search(s))


def strong_heading(s: str, caps: bool) -> bool:
    """A heading that may have text right under it: a keyword and a number, or capitals ending in a letter, a digit
    or a full stop."""
    if not heading_line(s, caps):
        return False
    if STRONG.match(s):
        return True
    return caps and bool(CAPS.match(s)) and (_letter(s[-1]) or "0" <= s[-1] <= "9" or s[-1] == ".")


def _indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip())


def _letter(c: str) -> bool:
    return _ru_lower(c) or _ru_upper(c) or _latin(c)


def items(text: str) -> list[list[str]]:
    """The text as [kind, text] items: "title" (a heading), "p" (a paragraph), "break" (a scene break)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("\ufeff")
    lines = [ln.rstrip() for ln in text.split("\n")]
    full = [ln for ln in lines if ln]
    if not full:
        return []
    lens = sorted(len(ln) for ln in full)
    width = lens[len(lens) * 9 // 10]  # nearly the longest line: the width of a hard-wrapped text
    long = sum(1 for n in lens if n > 90)
    wide = sum(1 for n in lens if 4 * n >= 3 * width)
    wrapped = long * 10 < len(full) * 3 and wide * 3 >= len(full)
    indents = Counter(_indent(ln) for ln in full)
    base = min(indents, key=lambda k: (-indents[k], k))  # the usual indent: only a deeper one starts a paragraph
    first = next(i for i, ln in enumerate(lines) if ln)
    gaps = sum(1 for i in range(first + 1, len(lines) - 1) if not lines[i] and lines[i + 1])  # blank runs inside
    spaced = gaps * 20 >= len(full)  # blank lines between paragraphs, not only around a few headings
    lower = sum(1 for c in text if _ru_lower(c) or "a" <= c <= "z")
    upper = sum(1 for c in text if _ru_upper(c) or "A" <= c <= "Z")
    caps = lower > upper  # in a text all in capitals, capitals say nothing
    vocab = vocabulary([[Line(ln, 0, 0, 0, 0) for ln in lines]])  # how the text spells words away from line ends

    def short(ln: str) -> bool:
        return 4 * len(ln) < 3 * width

    def apart(j: int) -> bool:  # the edge of the text, a blank line or a scene break
        return j < 0 or j >= len(lines) or not lines[j] or bool(SCENE_BREAK.match(SPACE.sub(" ", lines[j]).strip()))

    out: list[list[str]] = []
    buf: list[str] = []
    title_at = -2

    def flush() -> None:
        if buf:
            out.append(["p", " ".join(buf)])
            buf.clear()

    for i, ln in enumerate(lines):
        s = SPACE.sub(" ", ln).strip()
        if not s:
            flush()
            continue
        if SCENE_BREAK.match(s):
            flush()
            out.append(["break", ""])
            continue
        prev = lines[i - 1] if i else ""
        starts = not buf or not wrapped or _indent(ln) > base or (short(prev) and bool(ENDS_SENTENCE.search(prev)))
        if (
            spaced
            and not starts
            and apart(i + 1)
            and short(ln)
            and bool(ENDS_SENTENCE.search(prev))
            and bool(STRONG.match(s))
            and heading_line(s, caps)
        ):  # a chapter heading as the last line of a paragraph, right after the end of a sentence
            flush()
            out.append(["title", s])
            title_at = i
            continue
        if starts and heading_line(s, caps):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            before = apart(i - 1) or title_at == i - 1 or not spaced
            after = (
                apart(i + 1)
                or heading_line(SPACE.sub(" ", nxt).strip(), caps)
                or (not spaced and (not wrapped or _indent(nxt) > base or short(ln)))
                or (spaced and strong_heading(s, caps))  # the first line of a paragraph, text right under it
            )
            if before and after:
                flush()
                out.append(["title", s])
                title_at = i
                continue
        if starts:
            flush()
        last = buf[-1] if buf else ""
        if len(last) > 1 and last.endswith("-") and _letter(last[-2]) and (_ru_lower(s[0]) or "a" <= s[0] <= "z"):
            left, right = BROKEN.search(last)[1], LEADING.match(s)[0]  # a word hyphenated at the line end
            buf[-1] = last[:-1] + ("-" if keeps_hyphen(left, right, vocab) else "") + s
        else:
            buf.append(s)
    flush()
    return out


# ---------------------------------------------------------------- notes

CLOSERS = set(".,:;!?…»\"”')]")
OPENERS = set("«„“\"'([")
STAR_WINDOW = 3  # a note's body comes within this many paragraphs after its marker's (extract_fb2.STAR_WINDOW)


def _alnum(c: str) -> bool:
    return c.isalnum()


def star_runs(text: str) -> list[tuple[int, int, bool]]:
    """(start, count, paired) of each run of asterisks; `paired` when the run opens or closes a `*word*`
    emphasis: an opening run (at the start or after a space or an opening bracket, before text) and the run
    right after it with as many asterisks that closes (after text, before a space, punctuation or the end)."""
    runs: list[list] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "*":
            i += 1
            continue
        j = i
        while j < n and text[j] == "*":
            j += 1
        runs.append([i, j - i, False])
        i = j
    for a, b in zip(runs, runs[1:], strict=False):
        s, k = a[0], a[1]
        opens = (s == 0 or text[s - 1].isspace() or text[s - 1] in OPENERS) and s + k < n and not text[s + k].isspace()
        e = b[0] + b[1]
        closes = not text[b[0] - 1].isspace() and (e == n or text[e].isspace() or text[e] in CLOSERS)
        if not a[2] and opens and closes and b[1] == k:
            a[2] = b[2] = True
    return [(s, k, paired) for s, k, paired in runs]


def note_marks(text: str) -> list[tuple[int, int]]:
    """(start, count) of each `*`, `**` or `***` glued to the end of a word (or to its closing punctuation) and
    before a space, punctuation or the end, and not one of a `*word*` pair: the marker of an asterisk footnote."""
    out = []
    n = len(text)
    for i, k, paired in star_runs(text):
        j = i + k
        if (
            k <= 3
            and not paired
            and i > 0
            and (_alnum(text[i - 1]) or text[i - 1] in CLOSERS)
            and (j == n or text[j] == " " or text[j] in CLOSERS)
        ):
            out.append((i, k))
    return out


def note_body(text: str) -> tuple[int, str] | None:
    """A paragraph that starts with one to three asterisks and then text (not a `*word*` emphasis): (count, the
    note's text)."""
    k = len(text) - len(text.lstrip("*"))
    rest = text[k:].removeprefix(" ")
    if not 1 <= k <= 3 or not rest or rest[0] == "*" or rest[0] == " " or star_runs(text)[0][2]:
        return None
    return k, rest


def attach_notes(paras: list[list[str]]) -> tuple[dict[int, list[dict]], dict[str, str]]:
    """Asterisk footnotes: a body takes the latest marker before it with as many asterisks that has none yet, in
    one of the STAR_WINDOW paragraphs of text before it and in the same chapter. The body paragraph becomes kind
    "note" (left out of the text); returns the notes of each paragraph by index, with offsets in the paragraph's
    text once its markers are gone, and the book's notes."""
    pending: list[tuple[int, int, int, int]] = []  # (paragraph, start, count, its place among the text paragraphs)
    found: dict[int, list[tuple[int, int, str]]] = {}
    notes: dict[str, str] = {}
    seen = 0  # paragraphs of text so far
    for idx, it in enumerate(paras):
        if it[0] == "title":
            pending.clear()
        if it[0] != "p":
            continue
        body = note_body(it[1])
        if body:
            k, text = body
            near = seen - STAR_WINDOW
            j = next((x for x in range(len(pending) - 1, -1, -1) if pending[x][2] == k and pending[x][3] >= near), None)
            if j is not None:
                owner, start, _, _ = pending.pop(j)
                nid = f"n{len(notes) + 1}"
                notes[nid] = text
                found.setdefault(owner, []).append((start, k, nid))
                it[0] = "note"
                continue
        pending += [(idx, start, k, seen) for start, k in note_marks(it[1])]
        seen += 1
    refs: dict[int, list[dict]] = {}
    for idx, marks in found.items():
        text, out, cut, last = paras[idx][1], [], 0, 0
        new = ""
        for start, k, nid in sorted(marks):
            new += text[last:start]
            out.append({"pos": start - cut, "id": nid, "m": "*" * k})
            cut += k
            last = start + k
        paras[idx][1] = new + text[last:]
        refs[idx] = out
    return refs, notes


# ---------------------------------------------------------------- book


def assemble(paras: list[list[str]], title: str, author: str, refs: dict, notes: dict) -> dict:
    """[kind, text] items -> the book.json model: titles start chapters, a scene break is a gap before the next
    block, notes are left out (their markers are in `refs`)."""
    blocks: list[dict] = []
    chapters: list[dict] = [{"id": "s0", "title": "", "level": 1, "first_block": 0}]
    gap = 0
    for idx, (kind, text) in enumerate(paras):
        if kind == "break":
            gap += 1
            continue
        if kind == "note":
            continue
        if kind == "title":
            gap = 0  # a break before a chapter is not the chapter's
            chapters.append(
                {
                    "id": f"s{len(chapters)}",
                    "title": text,
                    "level": 1 if BIG_PART.match(text) else 2,
                    "first_block": len(blocks),
                }
            )
        block = {
            "images": [],
            "id": f"b{len(blocks)}",
            "kind": kind,
            "chapter": len(chapters) - 1,
            "stanza": None,
            "text": text,
            "em": [],
            "notes": refs.get(idx, []),
            "sentences": block_sentences(text, kind),
            "audio": True,
        }
        st = extract_style.block_style(kind, {}, {}, gap)
        if st:
            block["st"] = st
        gap = 0
        blocks.append(block)
    if chapters and not chapters[0]["title"] and (len(chapters) == 1 or chapters[1]["first_block"] == 0):
        chapters = chapters[1:] or chapters
        for b in blocks:
            b["chapter"] = max(0, b["chapter"] - 1)
    return {"title": title, "author": author, "chapters": chapters, "blocks": blocks, "notes": notes}


def extract(path: Path) -> dict:
    paras = items(read_text(path))
    refs, notes = attach_notes(paras)
    return assemble(paras, path.stem, "", refs, notes)


def build(paras: list[str], title: str, author: str = "") -> dict:
    """Paragraphs made elsewhere -> the book.json model, each paragraph standing alone."""
    caps = sum(1 for p in paras for c in p if _ru_lower(c) or "a" <= c <= "z") > sum(
        1 for p in paras for c in p if _ru_upper(c) or "A" <= c <= "Z"
    )
    return assemble([["title" if heading_line(p, caps) else "p", p] for p in paras], title, author, {}, {})


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="book.txt -> book.json")
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    src = next((f for f in args.book_dir.iterdir() if f.suffix.lower() == ".txt"), None)
    if src is None:
        raise SystemExit("no .txt in book dir")
    book = extract(src)
    (args.book_dir / "book.json").write_text(dump_book(book), encoding="utf-8")
    n_words = sum(len(b["text"].split()) for b in book["blocks"])
    print(f"chapters={len(book['chapters'])} blocks={len(book['blocks'])} words={n_words}")


if __name__ == "__main__":
    main()
