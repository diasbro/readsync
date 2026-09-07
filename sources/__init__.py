"""Text sources, one module each, searched in this order. A source is an object with a `name`,
`search(query) -> [hit]` and `author_books(query) -> (name, [hit])`; see `base.hit` for the row shape.
Adding a catalog: a new module and one entry in SOURCES."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .base import editions, norm_title
from .coollib import Coollib
from .fantasy_worlds import FantasyWorlds
from .flibusta import Flibusta

SOURCES = [FantasyWorlds(), Flibusta(), Coollib()]
LABELS = {"fantasy-worlds": "fantasy-worlds", "flibusta": "Flibusta", "coollib": "Coollib"}


def search_text(query: str) -> dict:
    """Every source in parallel: books by title, plus the books of an author named exactly like the
    query. Rows are editions as the catalogs describe them; the reader picks."""
    hits, by_author, errors = [], [], []
    author_name = ""
    want = norm_title(query)
    with ThreadPoolExecutor(max_workers=2 * len(SOURCES)) as ex:
        futures = {}
        for s in SOURCES:
            futures[ex.submit(s.search, query)] = (s.name, "title")
            futures[ex.submit(s.author_books, query)] = (s.name, "author")
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
    # a title hit whose author is the query belongs to the author block
    fw_by_author = [h for h in hits if want and norm_title(h["author"]) == want]
    if fw_by_author:
        author_name = author_name or fw_by_author[0]["author"]
        by_author = fw_by_author + by_author
        hits = [h for h in hits if h not in fw_by_author]
    return {
        "hits": editions(hits)[:30],
        "author": {"name": author_name, "hits": editions(by_author)[:80]} if author_name else None,
        "errors": errors,
    }
