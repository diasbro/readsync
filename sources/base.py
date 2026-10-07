"""Shared pieces for text sources: fetching, title normalisation, volume numbers, and the grouping
of raw hits into editions. A source module returns plain dicts (see `sources/__init__.py`)."""

from __future__ import annotations

import html
import re
import threading
import urllib.request

VOLUME_RE = re.compile(r"\b(?:т|том|кн|книга|ч|часть|vol|volume|part)\.?\s*(\d+|[IVXLC]+)\b", re.I)
OPENS = frozenset(("html", "fb2", "epub", "pdf", "txt"))  # what the pipeline can turn into a book
# priority when rows are sorted: the Russian catalogs first, then the open ones
SOURCE_ORDER = ("fantasy-worlds", "flibusta", "coollib", "standard-ebooks", "gutenberg", "wikisource", "bia")
# `over`: the running round's end, set by `sources.ask` in each request's thread
ROUND = threading.local()


def round_over() -> threading.Event:
    """The end of the round this thread's request belongs to; a fresh event outside a round."""
    return getattr(ROUND, "over", None) or threading.Event()


def get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def unescape(s: str) -> str:
    return html.unescape(s)


def norm_title(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = VOLUME_RE.sub(" ", s)
    s = re.sub(r"\bгл\.?\s.*$", " ", s)  # chapter ranges after the volume ("Гл. I - XL")
    s = re.sub(r"\[[^\]]*\]|\([^)]*$", " ", s)  # edition tags like [litres], [СИ]; dangling "("
    return re.sub(r"[^\w]+", " ", s).strip()


# words that carry no signal in a title search
STOP_WORDS = "и в во на с со по за как для о об от до из не ни то что это его их при над под без the a an of and or"
STOP = frozenset(STOP_WORDS.split())


def terms(query: str) -> list[str]:
    """Meaningful words of a query, longest first. The catalogs match a phrase inside a title, so a
    query that names more than the title finds nothing; these words are what to ask them instead."""
    seen: list[str] = []
    for w in sorted(norm_title(query).split(), key=len, reverse=True):
        if len(w) > 2 and w not in STOP and w not in seen:  # «Дао», «Цзы» are names, not noise
            seen.append(w)
    return seen


def said_words(query: str) -> list[str]:
    """The words of a query that judge a row: short ones too («Лунь юй»), but no stop words or initials."""
    return [w for w in norm_title(query).split() if len(w) > 1 and w not in STOP]


def fallbacks(query: str, limit: int = 3) -> list[str]:
    """Shorter searches for a query the catalogs cannot match as a phrase. A reader names the author
    before the title («Толстой Война и мир») or after it («…решений Виногродский»), so the query
    without its first word and without its last one are tried whole, as long as what is left could
    be a title. Then the first two words, and the longest single ones."""
    raw = [w for w in norm_title(query).split() if len(w) > 1 or w in STOP]  # no initials
    kept = [w for w in raw if w not in STOP]
    out: list[str] = []
    if len(kept) > 1:
        out.append(" ".join(kept[:2]))
    for side in (raw[1:], raw[:-1]):  # the author dropped from the front, then from the back
        if 1 < len(side) <= 4:
            out.append(" ".join(side))
    for w in terms(query):
        out.append(w)
    seen: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.append(t)
    return seen[:limit]


def matched(words: list[str], row: dict) -> int:
    """How many of the query's words a row names, in its title, author or translator. Whole words
    only, allowing a different ending («войны» names «война», «Владимир» does not name «мир»)."""
    said = norm_title(" ".join((row.get("title", ""), row.get("author", ""), row.get("translator", "")))).split()
    return sum(len(w) > 1 and any(same_word(w, x) for x in said) for w in words)


def same_word(a: str, b: str) -> bool:
    """The same word up to its ending: Russian declines, and a catalog title declines with it.
    «войны» names «война» and «мире» names «мир», «Владимир» names neither."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) < 3 or len(long_) - len(short) > (2 if len(short) == 3 else 3):
        return False
    stem = max(3, len(short) - 2)
    return short[:stem] == long_[:stem]


def title_score(query: str, words: list[str], row: dict) -> int:
    """How close a row is to what was typed. The title carries the most weight: a reader who types
    «Война и мир» wants that book, not a newer one that merely mentions the words."""
    want, title = norm_title(query), norm_title(row.get("title", ""))
    said = title.split()
    in_title = sum(any(same_word(w, x) for x in said) for w in words)
    exact = 6 if title == want else 4 if title.startswith(want) else 2 if want and want in title else 0
    # the title says nothing the query did not: «Война и мир» for "Толстой Война и мир", not an album about it
    asked = [w for w in want.split() if w not in STOP]
    named = [w for w in said if w not in STOP]
    covered = 5 if named and all(any(same_word(t, w) for w in asked) for t in named) else 0
    # nothing the query said is left unaccounted for, title or author alike: «Виногродский книга
    # перемен» is his book, not another author's book of that name
    answered = 6 if answers_whole_query(words, row) else 0
    return exact + covered + answered + 2 * in_title + matched(words, row)


def answers_whole_query(words: list[str], row: dict) -> bool:
    """Whether the row names every meaningful word of the query, in its title, author or translator."""
    return bool(words) and matched(words, row) == len(words)


def roman_to_int(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = vals.get(ch, 0)
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def volume_no(title: str) -> int | None:
    m = VOLUME_RE.search(title or "")
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else roman_to_int(v)


def hit(source: str, title: str, url: str, kind: str, **extra) -> dict:
    """A raw hit as a source reports it. Only facts the catalog states; nothing is judged here."""
    h = {
        "source": source,
        "title": title,
        "author": "",
        "translator": "",
        "year": "",
        "size_kb": None,
        "lang": "",
        "url": url,
        "kind": kind,
        "audio_url": "",
        "narrator": "",
        "readable": True,
    }
    h.update(extra)
    return h


def audio_hit(source: str, ref: str, title: str, **extra) -> dict:
    """A recording as an audio source reports it (see `sources/audio`). `ref` is all the server needs
    to find it again; sizes are estimates from duration and bitrate where the source states no size."""
    h = {
        "source": source,
        "ref": ref,
        "title": title,
        "author": "",
        "narrator": "",
        "duration_s": None,
        "parts": None,
        "size_bytes": None,
        "bitrate_kbps": None,
        "captions": False,
        "page_url": "",
        "licensed_trial": False,
    }
    h.update(extra)
    return h


def editions(hits: list[dict]) -> list[dict]:
    """One row per edition. Numbered volumes of the same edition (same source, work, author, translator)
    load together as one book; anything else stays a row of its own."""
    groups: dict[tuple, list[dict]] = {}
    for h in hits:
        key = (h["source"], norm_title(h["title"]), norm_title(h["author"]), norm_title(h.get("translator", "")))
        groups.setdefault(key, []).append(h)
    rows = []
    for parts in groups.values():
        volumes = sorted((p for p in parts if volume_no(p["title"])), key=lambda p: volume_no(p["title"]))
        for p in parts:
            if not volume_no(p["title"]):
                rows.append(dict(p, parts=[{"title": p["title"], "url": p["url"], "kind": p["kind"]}]))
        if volumes:
            seen: set = set()
            vols = [p for p in volumes if not (volume_no(p["title"]) in seen or seen.add(volume_no(p["title"])))]
            first = vols[0]
            base = re.sub(r"[\s.,:;(–—-]+$", "", VOLUME_RE.split(first["title"])[0]).strip() or first["title"]
            labels = [VOLUME_RE.search(p["title"]).group(0) for p in vols]
            rows.append(
                dict(
                    first,
                    title=base,
                    parts=[{"title": p["title"], "url": p["url"], "kind": p["kind"]} for p in vols],
                    parts_label=", ".join(labels),
                )
            )
    order = {n: i for i, n in enumerate(SOURCE_ORDER)}
    rows.sort(key=lambda r: (order.get(r["source"], 9), -int(r["year"] or 0), r["title"]))
    for r in rows:
        r.pop("url", None)
    return rows
