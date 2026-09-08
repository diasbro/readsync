"""Text sources, one module each, searched in this order. A source is an object with a `name`,
`search(query) -> [hit]` and `author_books(query) -> (name, [hit])`; see `base.hit` for the row shape.
Adding a catalog: a new module and one entry in SOURCES."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .base import editions, fallbacks, matched, norm_title, terms
from .coollib import Coollib
from .fantasy_worlds import FantasyWorlds
from .flibusta import Flibusta

SOURCES = [FantasyWorlds(), Flibusta(), Coollib()]
LABELS = {"fantasy-worlds": "fantasy-worlds", "flibusta": "Flibusta", "coollib": "Coollib"}
SHORTER_TRIES = 3  # how many shorter searches follow a phrase that found nothing


def ask(queries: list[str], authors: bool = True) -> tuple[list[dict], list[dict], str, list[str]]:
    """Every term against every source, all at once: title hits, an author's books, that author's
    name, errors. Shorter retries of a query ask for titles only: a word of a title is not a name."""
    hits: list[dict] = []
    by_author: list[dict] = []
    errors: list[str] = []
    author_name = ""
    with ThreadPoolExecutor(max_workers=len(SOURCES) * (len(queries) + bool(authors))) as ex:
        futures = {}
        for s in SOURCES:
            for query in queries:
                futures[ex.submit(s.search, query)] = (s.name, "title")
            if authors:
                futures[ex.submit(s.author_books, queries[0])] = (s.name, "author")
        for f, (n, what) in futures.items():
            try:
                res = f.result(timeout=60)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{n}: {e}")
                continue
            if what == "author":
                name, books = res
                author_name = author_name or name
                by_author += books
            else:
                hits += res
    return hits, by_author, author_name, errors


def unique(hits: list[dict]) -> list[dict]:
    """The same book can come back from several terms; one row per catalog link."""
    seen: set[tuple[str, str]] = set()
    out = []
    for h in hits:
        key = (h.get("source", ""), h.get("url", ""))
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def search_text(query: str) -> dict:
    """Every source in parallel: books by title, plus the books of an author the query names. The
    catalogs look for the phrase inside a title, so a query that says more than the title finds
    nothing; then shorter searches follow (the first two words, then the longest ones) and rows that
    name at least two words of the query are kept, closest first. Rows are editions as the catalogs
    describe them; the reader picks."""
    hits, by_author, author_name, errors = ask([query])
    words = terms(query)
    note = ""
    if not hits and words and len(norm_title(query).split()) > 1:
        note = "по названию целиком ничего; ниже то, что нашлось по словам"
        hits, _, _, errs = ask(fallbacks(query, SHORTER_TRIES), authors=False)
        errors += errs
        need = min(2, len(words))  # a single shared word is a coincidence, two are a match
        hits = [h for h in hits if matched(words, h) >= need]
    # a title hit by an author the query names belongs to the author block
    want = set(norm_title(query).split())
    by_query_author = [h for h in hits if want and want <= set(norm_title(h["author"]).split())]
    if by_query_author:
        author_name = author_name or by_query_author[0]["author"]
        by_author = by_query_author + by_author
        hits = [h for h in hits if h not in by_query_author]
    rows = editions(unique(hits))
    rows.sort(key=lambda r: -matched(words, r))  # closest to what was typed first, order within a score kept
    return {
        "note": note if rows else "",
        "hits": rows[:30],
        "author": {"name": author_name, "hits": editions(unique(by_author))[:80]} if author_name else None,
        "errors": errors,
    }
