"""Where the main text of a book ends: the reference matter at the back (bibliography, indexes, a
dictionary by letters, «Об авторе») is not reading, so a book counts as read once the text before it
is. Pure functions over book.json and timing.json; manifest.py stamps the result into book.toml, and
the reader and the phone only compare numbers.

Walking the chapters from the end, a chapter is back matter while its title (or the title of a chapter
it sits under) names reference matter, it is a letter of a dictionary in a run of them, or it is almost
empty. An epilogue, an afterword or a conclusion is always main text and stops the walk, and so does any
other chapter. Back matter of more than 35% of the book is a sign the rule is wrong: the book then ends
at its end.
"""

from __future__ import annotations

import re

TAIL = re.compile(
    r"примечани|комментари|библиограф|литератур|указател|словар|глоссари|об автор|о художник"
    r"|над книгой работал|благодарност|оглавлени|содержани|приложени|список сокращ"
    r"|summary|notes|index|bibliograph|acknowledg",
    re.IGNORECASE,
)
MAIN = re.compile(r"эпилог|послеслови|заключени|финал", re.IGNORECASE)
LETTER = re.compile(r"^[А-ЯЁA-Z][а-яёa-z]?\.?$")  # «Ш», «Цз», «Чж»: the heading of a dictionary's letter
LETTER_RUN = 3  # one short title near the end is a chapter («Мы»), several in a row are a dictionary
EMPTY = 0.001  # a chapter under 0.1% of the sentences is a heading or a colophon, not reading
MAX_TAIL = 0.35


def _bounds(book: dict) -> tuple[int, int]:
    """(first block of the back matter, its first sentence); both are the book's end when there is none."""
    blocks = book.get("blocks") or []
    chapters = [c for c in book.get("chapters") or [] if isinstance(c.get("first_block"), int)]
    counts = [len(b.get("sentences") or []) for b in blocks]
    total = sum(counts)
    starts = [min(max(0, c["first_block"]), len(blocks)) for c in chapters]
    ends = [*starts[1:], len(blocks)][: len(starts)]
    sizes = [sum(counts[a:e]) for a, e in zip(starts, ends, strict=True)]
    titles = [" ".join(str(c.get("title") or "").split()) for c in chapters]
    levels = [c.get("level") if isinstance(c.get("level"), int) else 1 for c in chapters]
    letter = [bool(LETTER.match(t)) for t in titles]

    def letter_run(j: int) -> int:
        a = b = j
        while a > 0 and letter[a - 1]:
            a -= 1
        while b + 1 < len(letter) and letter[b + 1]:
            b += 1
        return b - a + 1

    def under_tail(j: int) -> bool:
        """The title, or the title of a chapter this one sits under, names reference matter."""
        level = levels[j] + 1
        for k in range(j, -1, -1):
            if levels[k] < level:
                if TAIL.search(titles[k]):
                    return True
                level = levels[k]
        return False

    first = len(chapters)
    for j in range(len(chapters) - 1, -1, -1):
        if MAIN.search(titles[j]):
            break
        if under_tail(j) or (letter[j] and letter_run(j) >= LETTER_RUN) or sizes[j] < EMPTY * total:
            first = j
            continue
        break
    if first == len(chapters):
        return len(blocks), total
    block = starts[first]
    sent = sum(counts[:block])
    if total - sent > MAX_TAIL * total:
        return len(blocks), total
    return block, sent


def text_end(book: dict) -> int:
    """The index of the first sentence of the back matter; the number of sentences when there is none."""
    return _bounds(book)[1]


def audio_end(book: dict, timing: dict) -> float:
    """The end of the last word read before the back matter; the recording's duration when no word is."""
    block = _bounds(book)[0]
    ends = [w[4] for w in timing.get("words") or [] if len(w) >= 5 and w[0] < block]
    return float(max(ends)) if ends else float(timing.get("duration") or 0)
