"""archive.org, the texts: `advancedsearch.php` finds the items, `/metadata/<id>` names their files. Items of
the lending library (`access-restricted-item`, their files answer 401) and hidden collections are not
asked for. From an item: an EPUB its uploader made, else the OCR text, else an OCR'd PDF; never the
derived EPUB (a JPEG per page) or `Image Container PDF` (no text layer). Rows have to name the query
(see `standard_ebooks.relevant`)."""

from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import base
from .gutenberg import LANGS
from .ia import BASE, fetch, joined, parse_search
from .standard_ebooks import relevant

NAME = "archive.org"
ROWS = 12  # how many items get their metadata read; sorted by popularity, so the wanted one comes early
# (format, kind, only the uploader's own file), best first
FORMATS = (
    ("EPUB", "epub", True),
    ("DjVuTXT", "txt", False),
    ("Text PDF", "pdf", False),
    ("Additional Text PDF", "pdf", False),
)
ONLY_TEXT = " OR ".join(f'format:"{fmt}"' for fmt, _, _ in FORMATS)
HIDDEN = "NOT collection:deemphasize AND NOT collection:no-preview"
YEAR = re.compile(r"(1[0-9]{3}|20[0-9]{2})")
# archive.org states a language as a name ("English", "russian") or an ISO 639-2 code, B or T ("ger", "deu")
LANG3 = {
    "eng": "en",
    "rus": "ru",
    "ger": "de",
    "deu": "de",
    "fre": "fr",
    "fra": "fr",
    "spa": "es",
    "ita": "it",
    "dut": "nl",
    "nld": "nl",
    "por": "pt",
    "pol": "pl",
    "ukr": "uk",
    "chi": "zh",
    "zho": "zh",
    "jpn": "ja",
    "lat": "la",
    "gre": "el",
    "ell": "el",
    "heb": "he",
    "ara": "ar",
    "swe": "sv",
    "dan": "da",
    "fin": "fi",
    "hun": "hu",
    "cze": "cs",
    "ces": "cs",
    "tur": "tr",
    "yid": "yi",
    "kor": "ko",
}
NAMES = {**{k.lower(): v for k, v in LANGS.items()}, "ukrainian": "uk", "czech": "cs", "hebrew": "he"}


def lang_of(value: object) -> str:
    """The two-letter language of the first one the item states."""
    code = joined(value).split(",")[0].strip().lower()
    return LANG3.get(code) or NAMES.get(code) or (code if len(code) == 2 else "")


def safe_metadata(identifier: str) -> dict | None:
    """An item's metadata, or None when it does not answer. Here and not in `ia`, so tests can replace `fetch`."""
    try:
        return fetch(f"{BASE}/metadata/{identifier}")
    except Exception:  # noqa: BLE001 - a missing item, a broken answer, a dropped connection
        return None


class InternetArchive:
    name = NAME
    seconds = 30  # one search plus the metadata of its rows

    def lucene(self, query: str) -> str:
        """Every word of the query in a title or a creator, over items with a readable text file."""
        words = base.terms(query)[:6]  # only \w characters: nothing to escape
        if not words:
            return ""
        named = " AND ".join(f"(title:({w}) OR creator:({w}))" for w in words)
        return f"mediatype:texts AND ({ONLY_TEXT}) AND NOT access-restricted-item:true AND {HIDDEN} AND ({named})"

    def names_query(self, query: str, doc: dict) -> bool:
        """Whether a search row names the query: the search also answers with items that mention one word."""
        row = {"title": joined(doc.get("title")), "author": joined(doc.get("creator")), "translator": ""}
        return bool(relevant(query, [row]))

    def file_of(self, data: dict) -> tuple[str, str, int | None] | None:
        """(file name, kind, size in bytes) of the best text file of the item, or None: a lending-library
        item, no text, or several books of one kind in one item (no telling which is the one asked for)."""
        info = data.get("metadata")
        if isinstance(info, dict) and str(info.get("access-restricted-item") or "").lower() == "true":
            return None
        files = data.get("files")
        if not isinstance(files, list):
            return None
        for fmt, kind, original in FORMATS:
            found = [
                f
                for f in files
                if isinstance(f, dict)
                and str(f.get("format") or "") == fmt
                and str(f.get("name") or "")  # a nameless entry would open the item's directory listing
                and (not original or f.get("source") == "original")
            ]
            if len(found) > 1:
                return None
            if found:
                size = str(found[0].get("size") or "")
                return str(found[0]["name"]), kind, int(size) if size.isdigit() else None
        return None

    def to_hit(self, doc: dict, meta: object) -> dict | None:
        pick = self.file_of(meta) if isinstance(meta, dict) else None
        if not pick:
            return None
        name, kind, size = pick
        info = meta.get("metadata")
        info = info if isinstance(info, dict) else {}
        year = YEAR.search(str(info.get("year") or doc.get("year") or ""))
        title = joined(info.get("title") or doc.get("title"))
        volume = joined(info.get("volume")).strip()
        if volume:
            title = f"{title}, т. {volume}"
        return base.hit(
            self.name,
            title,
            f"{BASE}/download/{doc['identifier']}/{urllib.parse.quote(name)}",
            kind,
            author=joined(info.get("creator") or doc.get("creator")),
            year=year.group(1) if year else "",
            size_kb=size // 1024 if size else None,
            lang=lang_of(info.get("language")),
        )

    def search(self, query: str) -> list[dict]:
        q = self.lucene(query)
        if not q:
            return []
        over = base.round_over()
        fields = "".join(f"&fl[]={f}" for f in ("identifier", "title", "creator", "year", "downloads"))
        url = f"{BASE}/advancedsearch.php?q={urllib.parse.quote(q)}{fields}&rows={ROWS}&page=1"
        found = parse_search(fetch(url + "&sort[]=downloads+desc&output=json"))
        docs = [d for d in found if self.names_query(query, d)][:ROWS]

        def metadata(identifier: str) -> dict | None:
            return None if over.is_set() else safe_metadata(identifier)  # the round is over: no more requests

        with ThreadPoolExecutor(max_workers=2) as ex:  # the shared request slots
            metas = list(ex.map(metadata, [d["identifier"] for d in docs]))
        return [h for h in (self.to_hit(d, m) for d, m in zip(docs, metas, strict=True)) if h]

    def author_books(self, query: str) -> tuple[str, list[dict]]:
        return "", []  # the search matches creators too; search_text moves their books to the author block
