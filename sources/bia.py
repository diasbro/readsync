"""bia.or.th, the Buddhadasa Indapanno Archives: Buddhadasa Bhikkhu's books in many languages, given away
by the Dhammadana Foundation for free distribution, as PDF files found through the WordPress media
search. A title is the file's name; some files are scans with no text layer, which the pipeline
refuses with its own message."""

from __future__ import annotations

import json
import re
import urllib.parse

from .base import get, hit, unescape
from .standard_ebooks import relevant

SEARCH = (
    "https://main.bia.or.th/wp-json/wp/v2/media?mime_type=application/pdf&per_page=20"
    "&_fields=title,source_url,media_details&search="
)
AUTHOR = "Buddhadasa Bhikkhu"
LANGS = {"CN": "zh"}  # the files name a country where a language is meant


class Bia:
    name = "bia"

    def files(self, items: list[dict]) -> list[dict]:
        hits = []
        for m in items:
            url = m.get("source_url") or ""
            raw = unescape(((m.get("title") or {}).get("rendered")) or "")
            if not url.lower().endswith(".pdf") or not raw:
                continue
            title = re.sub(r"(?<=\w)-(?=\w)", " ", raw)  # file names: words joined by hyphens
            lang = ""
            code = re.search(r"[\s-]([A-Z]{2})$", title)  # «…-Buddhadasa-ID»: the language the file is in
            if code:
                title, lang = title[: code.start()], LANGS.get(code.group(1), code.group(1).lower())
            author = AUTHOR if re.search(r"buddhadasa", title, re.I) else ""
            title = re.sub(r"[\s-]*Buddhadasa(\s+Bhikkhu)?\b", " ", title, flags=re.I)
            title = re.sub(r"\s+", " ", title).strip(" -") or raw
            size = (m.get("media_details") or {}).get("filesize")
            hits.append(
                hit(self.name, title, url, "pdf", author=author, lang=lang, size_kb=int(size) // 1024 if size else None)
            )
        return hits

    def search(self, query: str) -> list[dict]:
        items = json.loads(get(SEARCH + urllib.parse.quote(query), timeout=30))
        return relevant(query, self.files(items if isinstance(items, list) else []))

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # one author's archive: its search is the title search
