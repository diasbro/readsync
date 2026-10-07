"""Wikisource (ru and en): public-domain and freely licensed texts, the MediaWiki search, and the whole
work as an EPUB from ws-export, chapters (subpages) included. A Cyrillic query asks the Russian
Wikisource, any other the English one. The search reads the text too, and a work's chapters and
encyclopedia articles are pages of their own: only whole works that name the query stay."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from .base import hit
from .standard_ebooks import relevant

API = "https://{lang}.wikisource.org/w/api.php?"
EXPORT = "https://ws-export.wmcloud.org/?lang={lang}&format=epub-3&page={page}"


# Wikimedia asks clients to name themselves; a browser's name gets throttled sooner
AGENT = "readsync/1.0 (personal e-book reader; one search per query)"


def get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def language(query: str) -> str:
    return "ru" if re.search(r"[а-яё]", query, re.I) else "en"


class Wikisource:
    name = "wikisource"

    def pages(self, data: dict, lang: str) -> list[dict]:
        hits = []
        found = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
        for p in found:
            title = p.get("title") or ""
            if "/" in title or "disambiguation" in (p.get("pageprops") or {}):
                continue  # a chapter or an article of a larger work, or a list of editions
            page = urllib.parse.quote(title.replace(" ", "_"), safe="()_,'")
            hits.append(hit(self.name, title, EXPORT.format(lang=lang, page=page), "epub", lang=lang))
        return hits

    def search(self, query: str) -> list[dict]:
        lang = language(query)
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "0",
            "gsrlimit": "20",
            "prop": "pageprops",
            "format": "json",
            "formatversion": "2",
        }
        data = json.loads(get(API.format(lang=lang) + urllib.parse.urlencode(params), timeout=30))
        return relevant(query, self.pages(data, lang))

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # an author is a page in another namespace; the title search is what the reader gets
