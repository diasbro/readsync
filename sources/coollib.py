from __future__ import annotations

from .opds import OpdsSource


class Coollib(OpdsSource):
    name = "coollib"
    base = "https://coollib.net"
    search_url = "https://coollib.net/opds/search?searchType=books&searchTerm="
