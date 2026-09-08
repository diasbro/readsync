"""OPDS catalogs (Flibusta, Coollib and the like): search by title, and the books of an author."""

from __future__ import annotations

import re
import urllib.parse

from .base import get, hit, norm_title, unescape

AUTHOR_PAGES = 3  # 20 books per OPDS page
SEARCH_PAGES = 2


class OpdsSource:
    name = "opds"
    base = ""
    search_url = ""  # search feed; the query is appended

    def entries(self, xml: str) -> list[dict]:
        """Book entries of a feed as hits, with what the catalog states: authors, translator, year, size, format."""
        hits = []
        for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            title = unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip() if "<title>" in e else ""
            authors = [unescape(x).strip() for x in re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)]
            lang = re.search(r"<dc:language>(.*?)</dc:language>", e)
            fb2 = re.search(r'href="([^"]+)"[^>]*type="application/fb2\+zip"', e) or re.search(
                r'type="application/fb2\+zip"[^>]*href="([^"]+)"', e
            )
            epub = re.search(r'href="([^"]+)"[^>]*type="application/epub\+zip"', e)
            link = fb2 or epub
            if link:
                href = link.group(1)
            else:  # some catalogs link the book page (or only its cover) from lists: derive /b/<id>/fb2
                page = re.search(r'href="(?:https?://[^/"]+)?/b/(\d+)"', e)
                page = page or re.search(r'href="[^"]*/i/\d+/(\d+)/cover', e)
                if not page:
                    continue
                href = f"/b/{page.group(1)}/fb2"
                fb2 = True
            if href.startswith("/"):
                href = self.base + href
            content = re.search(r"<content[^>]*>(.*?)</content>", e, re.S)
            info = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(content.group(1)))) if content else ""
            translator = re.search(r"Перевод(?:чик)?:\s*(.+?)\s*(?:Год издания|Формат|Язык|Размер|Скачиваний|$)", info)
            year = re.search(r"Год издания:\s*(\d{4})", info)
            size = re.search(r"Размер:\s*(\d+)\s*[KК][bБ]", info)
            fmt = re.search(
                r"Формат:\s*([a-z0-9]+)", info, re.I
            )  # the catalog's format; its "fb2" link may wrap an rtf
            hits.append(
                hit(
                    self.name,
                    title,
                    href,
                    fmt.group(1).lower() if fmt else "fb2" if fb2 else "epub",
                    author=", ".join(authors),
                    translator=translator.group(1).strip(" .,;") if translator else "",
                    year=year.group(1) if year else "",
                    size_kb=int(size.group(1)) if size else None,
                    lang=lang.group(1) if lang else "",
                )
            )
        return hits

    def search(self, query: str) -> list[dict]:
        """The feed holds 20 books a page and the book wanted is often not on the first one."""
        return self.pages(self.search_url + urllib.parse.quote(query), SEARCH_PAGES)[:40]

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        """(author name, books) when the catalog has an author the query names. A surname is enough:
        every word of the query has to be part of the name, so «виногродский» finds
        «Виногродский Бронислав Брониславович», and the shortest such name wins."""
        xml = get(f"{self.base}/opds/search?searchType=authors&searchTerm=" + urllib.parse.quote(query)).decode(
            "utf-8", "replace"
        )
        want = set(norm_title(query).split())
        best: tuple[int, str, str] | None = None
        for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            title = unescape(re.search(r"<title>(.*?)</title>", e, re.S).group(1)).strip() if "<title>" in e else ""
            link = re.search(r'href="([^"]*/opds/author/\d+)"', e)
            name = set(norm_title(title).split())
            if not link or not want or not want <= name:
                continue
            # namesakes: the catalog says how many books each has, and the reader means the one with many
            count = re.search(r"(\d+)\s*книг", re.sub(r"<[^>]+>", " ", unescape(e)), re.I)
            score = int(count.group(1)) if count else 0
            if best is None or score > best[0]:
                best = (score, title, link.group(1))
        if not best:
            return "", []
        _, title, url = best
        if url.startswith("/"):
            url = self.base + url
        for page_url in (url + "/alphabet", url):  # flibusta lists books under /alphabet, coollib on the page
            books = self.pages(page_url)
            if books:
                return title, books
        return title, []

    def pages(self, url: str | None, limit: int = AUTHOR_PAGES) -> list[dict]:
        books: list[dict] = []
        for _ in range(limit):
            if not url:
                break
            page = get(url).decode("utf-8", "replace")
            books += self.entries(page)
            m = re.search(r'<link[^>]*href="([^"]+)"[^>]*rel="next"', page) or re.search(
                r'<link[^>]*rel="next"[^>]*href="([^"]+)"', page
            )
            nxt = unescape(m.group(1)) if m else ""  # the href is XML-escaped: &amp; would ask for page one forever
            url = (self.base + nxt if nxt.startswith("/") else nxt) if nxt else None
        return books
