"""standardebooks.org: carefully produced public-domain books (CC0), an open OPDS search, EPUB files.
The search also reads the blurbs, so a row has to name the query to stay."""

from __future__ import annotations

import re
import unicodedata
import urllib.parse

from .base import get, hit, matched, norm_title, terms, unescape

SEARCH = "https://standardebooks.org/feeds/opds/all?per-page=24&query="


def fold(s: str) -> str:
    """Without accents, so «Tâo» is «Tao» and «Lao Tzŭ» is «Lao Tzu»."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def relevant(query: str, hits: list[dict]) -> list[dict]:
    """The rows that name the query: the phrase inside the title, or enough of its words in the title or
    author (two, or one for a one-word query). Catalogs that search full text return a lot besides."""
    want = norm_title(fold(query))
    words = terms(fold(query))
    need = min(2, len(words))
    out = []
    for h in hits:
        folded = dict(h, title=fold(h["title"]), author=fold(h["author"]), translator=fold(h.get("translator", "")))
        # the phrase as whole words: «ching» is not in «Quenching»
        if (want and f" {want} " in f" {norm_title(folded['title'])} ") or (need and matched(words, folded) >= need):
            out.append(h)
    return out


class StandardEbooks:
    name = "standard-ebooks"

    def entries(self, xml: str) -> list[dict]:
        hits = []
        for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            title = re.search(r"<title>(.*?)</title>", e, re.S)
            epub = re.search(r'<link href="([^"]+)"(?=[^>]*title="Recommended compatible epub")[^>]*>', e)
            if not title or not epub:
                continue
            length = re.search(r'length="(\d+)"', epub.group(0))
            page = re.search(r"<id>https://standardebooks\.org/ebooks/([^<]+)</id>", e)
            # the book's address is author/title[/translator]: a third part names the translator
            slug = page.group(1).split("/") if page else []
            translator = slug[2].replace("-", " ").title() if len(slug) > 2 else ""
            authors = [unescape(x).strip() for x in re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)]
            issued = re.search(r"<dc:issued>(\d{4})", e)
            lang = re.search(r"<dc:language>([a-z]+)", e)
            hits.append(
                hit(
                    self.name,
                    unescape(title.group(1)).strip(),
                    unescape(epub.group(1)),
                    "epub",
                    author=", ".join(authors),
                    translator=translator,
                    year=issued.group(1) if issued else "",
                    size_kb=int(length.group(1)) // 1024 if length else None,
                    lang=lang.group(1) if lang else "",
                )
            )
        return hits

    def search(self, query: str) -> list[dict]:
        xml = get(SEARCH + urllib.parse.quote(query), timeout=30).decode("utf-8", "replace")
        return relevant(query, self.entries(xml))

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # the search matches authors too; search_text moves their books to the author block
