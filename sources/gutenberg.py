"""gutenberg.org: public-domain books (in the USA), its own OPDS search, EPUB files. The search
matches the words of a query in titles, authors and subjects: rows that name the query are kept, and
when none does the catalog may have matched another spelling of the title («Tao Teh King» for «Tao Te
Ching»), so the rows that share at least a word with the query stay. Recordings share the catalog
and the feed does not mark them: their EPUB link answers 404."""

from __future__ import annotations

import re
import urllib.parse

from .base import get, hit, matched, terms, unescape
from .standard_ebooks import fold, relevant

SEARCH = "https://www.gutenberg.org/ebooks/search.opds/?query="
EPUB = "https://www.gutenberg.org/ebooks/{id}.epub3.images"
# a book in another language says so after its title: «道德經 (Chinese)»
LANGS = {
    "Chinese": "zh",
    "Danish": "da",
    "Dutch": "nl",
    "English": "en",
    "Finnish": "fi",
    "French": "fr",
    "German": "de",
    "Greek": "el",
    "Hungarian": "hu",
    "Italian": "it",
    "Japanese": "ja",
    "Latin": "la",
    "Polish": "pl",
    "Portuguese": "pt",
    "Russian": "ru",
    "Spanish": "es",
    "Swedish": "sv",
}


class Gutenberg:
    name = "gutenberg"

    def entries(self, xml: str) -> list[dict]:
        hits = []
        for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            book = re.search(r"<id>[^<]*/ebooks/(\d+)\.opds</id>", e)
            title = re.search(r"<title>(.*?)</title>", e, re.S)
            if not book or not title:
                continue  # "No records found." comes as an entry without a book
            name = unescape(title.group(1)).strip()
            lang = ""
            m = re.search(r"\s*\(([A-Z][a-z]+)\)$", name)
            if m:
                name, lang = name[: m.start()], LANGS.get(m.group(1), m.group(1).lower())
            content = re.search(r"<content[^>]*>(.*?)</content>", e, re.S)
            author = unescape(content.group(1)).strip() if content else ""
            if re.fullmatch(r"\d+ downloads?", author):  # an anonymous book shows its download count there
                author = ""
            hits.append(hit(self.name, name, EPUB.format(id=book.group(1)), "epub", author=author, lang=lang))
        return hits

    def search(self, query: str) -> list[dict]:
        xml = get(SEARCH + urllib.parse.quote_plus(query), timeout=30).decode("utf-8", "replace")
        hits = self.entries(xml)
        words = terms(fold(query))
        return relevant(query, hits) or [h for h in hits if matched(words, dict(h, title=fold(h["title"]))) >= 1]

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # the search matches authors too; search_text moves their books to the author block
