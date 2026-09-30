"""Text sources, one module each, searched in this order. A source is an object with a `name`,
`search(query) -> [hit]` and `author_books(query) -> (name, [hit])`; see `base.hit` for the row shape.
Adding a catalog: a new module and one entry in SOURCES."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait

from .base import OPENS, SOURCE_ORDER, editions, fallbacks, matched, norm_title, terms, title_score
from .coollib import Coollib
from .fantasy_worlds import FantasyWorlds
from .flibusta import Flibusta

SOURCES = [FantasyWorlds(), Flibusta(), Coollib()]
SHORTER_TRIES = 3  # how many shorter searches follow a phrase that found nothing
ROUND_SECONDS = 45  # one deadline for a whole round of requests, not one per request
PER_SOURCE = 2  # at most this many requests to one catalog at a time: more and the mirrors answer 502
CACHE_SECONDS = 900  # the same query asked again within this comes back without touching the network


def ask(queries: list[str], author_query: str = "") -> tuple[list[dict], list[dict], str, list[str]]:
    """Every term against every source, all at once: title hits, plus the books of an author named by
    `author_query` (the whole query at first, its most distinctive word when that found nothing)."""
    hits: list[dict] = []
    by_author: list[dict] = []
    errors: list[str] = []
    author_name = ""
    with ThreadPoolExecutor(max_workers=len(SOURCES) * PER_SOURCE) as ex:
        futures = {}
        for s in SOURCES:
            for query in queries:
                futures[ex.submit(s.search, query)] = (s.name, "title")
            if author_query:
                futures[ex.submit(s.author_books, author_query)] = (s.name, "author")
        done, late = wait(futures, timeout=ROUND_SECONDS)
        for f in late:
            f.cancel()
            errors.append(f"{futures[f][0]}: не ответил за {ROUND_SECONDS} с")
        for f in futures:  # in the order they were submitted, so the same search returns the same list
            if f not in done:
                continue
            n, what = futures[f]
            try:
                res = f.result()
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


def unique(hits: list[dict], dropped: list[int] | None = None) -> list[dict]:
    """One row per book: the same link comes back from several terms, and Coollib largely mirrors
    Flibusta, so a file that matches down to its size is the same file. Only formats the pipeline
    can open stay: a row nothing can be done with is noise in the list. `dropped` counts the rows
    thrown out for their format alone, so the reader can be told the book is there but unreadable."""
    links: set[tuple[str, str]] = set()
    files: set[tuple] = set()
    out = []
    order = {n: i for i, n in enumerate(SOURCE_ORDER)}
    for h in sorted(hits, key=lambda h: order.get(h.get("source", ""), 9)):  # the mirror kept is the preferred one
        if h.get("kind") not in OPENS:
            if dropped is not None and (h.get("source", ""), h.get("url", "")) not in links:
                dropped[0] += 1
            continue
        link = (h.get("source", ""), h.get("url", ""))
        same = (
            norm_title(h.get("title", "")),
            norm_title(h.get("author", "")),
            norm_title(h.get("translator", "")),
            h.get("size_kb"),
            h.get("kind"),
        )
        if link in links or (h.get("size_kb") and same in files):
            continue
        links.add(link)
        files.add(same)
        out.append(h)
    return out


CACHE: dict[str, tuple[float, dict]] = {}


def search_text(query: str) -> dict:
    """Every source in parallel: books by title, plus the books of an author the query names. The
    catalogs look for the phrase inside a title, so a query that says more than the title finds
    nothing; then shorter searches follow (the first two words, then the longest ones) and rows that
    name at least two words of the query are kept, closest first. Rows are editions as the catalogs
    describe them; the reader picks."""
    # the query as typed, less case and spacing: norm_title drops volume numbers, and «Книга 1» and
    # «Книга 2» must not share an answer
    key = " ".join(query.lower().replace("ё", "е").split())
    cached = CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]  # trying another wording is the usual loop: do not ask the mirrors again for the same one
    hits, by_author, author_name, errors = ask([query], author_query=query)
    words = terms(query)
    note = ""
    if not hits and words and len(norm_title(query).split()) > 1:
        note = "по названию целиком ничего; ниже то, что нашлось по словам"
        # a query like "технология принятия решений виногродский" names a book and its author at once
        hits, more_by_author, name, errs = ask(fallbacks(query, SHORTER_TRIES), author_query=words[0])
        by_author += more_by_author
        author_name = author_name or name
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
    dropped = [0]
    rows = editions(unique(hits, dropped))
    rows.sort(key=lambda r: -title_score(query, words, r))  # closest to what was typed first, year decides ties
    # the author's own shelf is long: the books whose titles answer the query stand at its front
    by_author_rows = editions(unique(by_author, dropped))
    by_author_rows.sort(key=lambda r: -title_score(query, words, r))
    out = {
        "note": note if rows else "",
        "hits": rows[:30],
        "author": {"name": author_name, "hits": by_author_rows[:80]} if author_name else None,
        "unopenable": dropped[0],  # found, but in a format the pipeline cannot turn into a book
        "errors": errors,
    }
    if not errors:  # a failed round is worth retrying, a good one is not
        CACHE[key] = (time.time(), out)
    return out
