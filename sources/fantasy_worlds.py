"""fantasy-worlds.org: a JSON search; the text is a reader page, an audiobook link may come with it."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .base import get, hit, norm_title

SEARCH = "https://fantasy-worlds.org/search.json?q="
READER = "https://reader.fantasy-worlds.org/book/{id}/read.html"


class FantasyWorlds:
    name = "fantasy-worlds"

    def search(self, query: str) -> list[dict]:
        data = json.loads(get(SEARCH + urllib.parse.quote(query)).decode("utf-8", "replace"))
        hits = []
        for b in (data.get("books") or [])[:10]:
            yt = b.get("yt_id")
            hits.append(
                hit(
                    self.name,
                    b.get("title") or "",
                    READER.format(id=b.get("id")),
                    "html",
                    author=" ".join(x for x in (b.get("author_name"), b.get("author_surname")) if x),
                    year=str(b.get("year") or ""),
                    lang=b.get("lang_code") or "",
                    audio_url=f"https://www.youtube.com/watch?v={yt}" if yt else "",
                    narrator=b.get("yt_reader") or "",
                )
            )
        # the site also matches series names and authors: keep hits that name the query in the title
        # or are by an author named like it (those go to the author block)
        want = norm_title(query)
        named = [h for h in hits if want in norm_title(h["title"]) or norm_title(h["author"]) == want]
        hits = named or hits

        def readable(h):
            try:
                r = urllib.request.Request(h["url"], method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
                h["readable"] = urllib.request.urlopen(r, timeout=8).status == 200
            except Exception:  # noqa: BLE001
                h["readable"] = False
            return h

        with ThreadPoolExecutor(max_workers=6) as ex:
            return list(ex.map(readable, hits))

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # the JSON search already returns an author's books; search_text sorts them out
