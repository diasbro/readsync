from __future__ import annotations

from .opds import OpdsSource


class Flibusta(OpdsSource):
    name = "flibusta"
    base = "https://flibusta.is"
    search_url = "https://flibusta.is/opds/search?searchType=books&searchTerm="
