"""A block's own look, as the book gives it: book.json's optional `st` on a block.

  "st": {"a": "l"|"r"|"c"|"j",  # text-align
         "i": 1.5,              # first-line indent, em (may be 0)
         "m": 2,                # left margin, em
         "g": 1}                # blank lines before the block

Every key is optional and `st` is left out when the book says nothing the block's kind does not already say.
Lengths are em in tenths, written as an int when whole. The style never touches a block's text, spans or
chapters: the extractors work exactly as without it (tests/test_styles.py holds them to that).

EPUB: text-align, text-indent, margin-left and padding-left from the book's CSS (class and element selectors
only: `p`, `.x`, `p.x.y`; anything else is skipped) and the inline style; text-align and text-indent inherit
from the containers, as in CSS. FB2: its kinds already carry their look in the reader, only `<empty-line/>`
adds to it. ios/Sources/Import/Style.swift is the same code on the phone and must stay byte for byte equal.
"""

from __future__ import annotations

import math
import re

ENABLED = True  # tests turn it off to compare the text with and without styles

ALIGN = {"left": "l", "start": "l", "right": "r", "end": "r", "center": "c", "justify": "j"}
# what a kind already looks like in the reader: the same value from the book is not repeated
KIND_ALIGN = {"title": "c", "subtitle": "c", "author": "r", "verse": "l"}
HEADINGS = ("title", "subtitle")  # styled on their own: an inherited body alignment is not theirs
INDENT_MAX, INDENT_IGNORE = 30, 100  # tenths of an em: clamped to the first, a trick beyond the second
MARGIN_MAX, MARGIN_IGNORE = 100, 300
GAP_MAX = 3
LENGTH = re.compile(r"([0-9]*\.?[0-9]+)(em|rem|px|pt|%)?")
SELECTOR = re.compile(r"([A-Za-z][A-Za-z0-9]*)?((?:\.[A-Za-z0-9_-]+)*)")


def tenths(value: str) -> int | None:
    """A CSS length in tenths of an em: px and pt at 16px to the em, % of a line about 30em long."""
    m = LENGTH.fullmatch(value)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        return 0 if v == 0 else None
    if unit in ("em", "rem"):
        t = v * 10
    elif unit == "px":
        t = v * 10 / 16
    elif unit == "pt":
        t = v * 10 / 12
    else:
        t = v * 3
    return math.floor(min(t, 1e6) + 0.5)


def declarations(text: str) -> dict:
    """The declarations that matter here: {"a": align, "i": indent, "ml": margin-left, "pl": padding-left}."""
    out: dict = {}
    for decl in text.split(";"):
        prop, sep, value = decl.partition(":")
        if not sep:
            continue
        prop = prop.strip().lower()
        value = value.lower().replace("!important", "").strip()
        if prop == "text-align":
            if value in ALIGN:
                out["a"] = ALIGN[value]
            continue
        if prop not in ("text-indent", "margin-left", "padding-left", "margin", "padding"):
            continue
        if prop in ("margin", "padding"):
            parts = value.split()
            if not 1 <= len(parts) <= 4:
                continue
            value = parts[(0, 1, 1, 3)[len(parts) - 1]]
        t = tenths(value)
        if t is not None:
            out["i" if prop == "text-indent" else "ml" if prop.startswith("margin") else "pl"] = t
    return out


def rules(css: str) -> list[tuple[str, str]]:
    """(selector list, declarations) of the top-level rules; at-rules (@media, @font-face…) are skipped. Comments
    go; a quoted string is kept whole, the braces and semicolons in it are its text."""
    css = css.lstrip("\ufeff")
    out: list[tuple[str, str]] = []
    depth = 0
    prelude: list[str] = []
    body: list[str] = []
    sel = ""
    nested = False  # the rule holds rules of its own
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        i += 1
        if ch == "/" and css.startswith("*", i):
            end = css.find("*/", i + 1)
            i = n if end < 0 else end + 2
            continue
        if ch in "\"'":
            j = i
            while j < n and css[j] != ch and css[j] != "\n":
                j += 2 if css[j] == "\\" else 1
            j = min(j + 1, n) if j < n and css[j] == ch else min(j, n)
            (prelude if depth == 0 else body).append(css[i - 1 : j])
            i = j
            continue
        if depth == 0:
            if ch == "{":
                depth = 1
                sel = "".join(prelude).strip()
                prelude, body, nested = [], [], False
            elif ch in ";}":
                prelude = []
            else:
                prelude.append(ch)
        elif ch == "{":
            depth += 1
            nested = True
            body.append(ch)
        elif ch == "}":
            depth -= 1
            if depth == 0:
                text = "".join(body)
                if not sel.startswith("@") and not nested:
                    out.append((sel, text))
            else:
                body.append(ch)
        else:
            body.append(ch)
    return out


class Sheet:
    """The rules of a document's stylesheets, in order; `own` is what applies to one element."""

    def __init__(self, sheets: list[str]) -> None:
        self.rules: list[tuple[int, int, str | None, set[str], dict]] = []
        for css in sheets:
            for sel_list, body in rules(css):
                decls = declarations(body)
                if not decls:
                    continue
                for sel in sel_list.split(","):
                    m = SELECTOR.fullmatch(sel.strip())
                    if not m or not (m.group(1) or m.group(2)):
                        continue
                    elem = m.group(1).lower() if m.group(1) else None
                    classes = set(m.group(2).split(".")[1:])
                    spec = len(classes) * 10 + (1 if elem else 0)
                    self.rules.append((spec, len(self.rules), elem, classes, decls))
        self.cache: dict[tuple[str, str], dict] = {}

    def own(self, name: str, classes: list[str], inline: str | None) -> dict:
        key = (name, " ".join(classes))
        out = self.cache.get(key)
        if out is None:
            have = set(classes)
            matched = sorted(r for r in self.rules if (r[2] is None or r[2] == name) and r[3] <= have)
            out = {}
            for r in matched:
                out.update(r[4])
            self.cache[key] = out
        if inline:
            out = {**out, **declarations(inline)}
        return out


def inherit(ctx: dict, own: dict) -> dict:
    """text-align and text-indent pass on to the elements inside, margins do not."""
    return {k: own.get(k, ctx.get(k)) for k in ("a", "i") if own.get(k, ctx.get(k)) is not None}


def num(t: int) -> int | float:
    return t // 10 if t % 10 == 0 else t / 10


def block_style(kind: str, own: dict, ctx: dict, gap: int = 0) -> dict | None:
    """`st` for a block of this kind: its own declarations, those it inherits, the blank lines before it."""
    if not ENABLED:
        return None
    src = own if kind in HEADINGS else {**ctx, **{k: v for k, v in own.items() if k in ("a", "i")}}
    st: dict = {}
    a = src.get("a")
    if a and a != KIND_ALIGN.get(kind):
        st["a"] = a
    i = src.get("i")
    if i is not None and i <= INDENT_IGNORE and (i > 0 or kind == "p"):
        st["i"] = num(min(i, INDENT_MAX))
    m = own.get("ml", 0) + own.get("pl", 0)
    if 0 < m <= MARGIN_IGNORE:
        st["m"] = num(min(m, MARGIN_MAX))
    if gap > 0:
        st["g"] = min(gap, GAP_MAX)
    return st or None
