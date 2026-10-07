"""Text sources: OPDS parsing and the grouping of raw hits into editions. No network."""

from __future__ import annotations

import sources
from sources import base
from sources.flibusta import Flibusta

ENTRY = """<entry><title>Дао Дэ Цзин</title><author><name>Лао-цзы</name></author>
<dc:language>ru</dc:language>
<content type="text/html">Перевод: Хин-шун Ян&lt;br/&gt;Год издания: 1972&lt;br/&gt;Формат: fb2&lt;br/&gt;Размер: 86 Kb</content>
<link href="/b/70131/fb2" rel="http://opds-spec.org/acquisition/open-access" type="application/fb2+zip" />
</entry>
<entry><title>Дао Дэ Цзин</title><author><name>Лао-цзы</name></author>
<content type="text/html">Перевод: Полежаева Юлия&lt;br/&gt;Формат: rtf&lt;br/&gt;Размер: 75 Kb</content>
<link href="/b/70132/fb2" type="application/fb2+zip" />
</entry>
<entry><title>Троецарствие. Том 1</title><author><name>Ло Гуань-чжун</name></author>
<link href="/b/1/fb2" type="application/fb2+zip" /></entry>
<entry><title>Троецарствие. Том 2</title><author><name>Ло Гуань-чжун</name></author>
<link href="/b/2/fb2" type="application/fb2+zip" /></entry>"""


def test_opds_entries_keep_catalog_facts():
    hits = Flibusta().entries(ENTRY)
    assert [h["translator"] for h in hits[:2]] == ["Хин-шун Ян", "Полежаева Юлия"]
    assert hits[0]["year"] == "1972" and hits[0]["size_kb"] == 86 and hits[0]["kind"] == "fb2"
    assert hits[1]["kind"] == "rtf"  # what the catalog says, not what the link promises
    assert hits[0]["url"] == "https://flibusta.is/b/70131/fb2"


def test_editions_group_volumes_but_not_translations():
    rows = base.editions(Flibusta().entries(ENTRY))
    dao = [r for r in rows if r["title"] == "Дао Дэ Цзин"]
    assert len(dao) == 2  # two translators, two rows
    vols = next(r for r in rows if r["title"] == "Троецарствие")
    assert [p["url"] for p in vols["parts"]] == ["https://flibusta.is/b/1/fb2", "https://flibusta.is/b/2/fb2"]
    assert vols["parts_label"] == "Том 1, Том 2"


AUTHORS_FEED = """<feed>
<entry><title>Виногродская Анна</title><link href="/opds/author/1"/></entry>
<entry><title>Виногродский Бронислав Брониславович</title><link href="/opds/author/2"/></entry>
</feed>"""


def test_query_words_and_shorter_searches():
    q = "Книга перемен как технология принятия решений"
    assert base.terms(q) == ["технология", "принятия", "перемен", "решений", "книга"]
    # a title starts with the name of the work, so the first two words are tried before single ones
    assert base.fallbacks(q) == ["книга перемен", "технология", "принятия"]
    # the author can lead or trail: dropping the first word, then the last, keeps the title whole
    assert base.fallbacks("Толстой Война и мир") == ["толстой война", "война и мир", "толстой война и"]
    assert base.fallbacks("технология принятия решений виногродский")[2] == "технология принятия решений"


def test_matched_counts_the_words_a_row_names():
    words = base.terms("Книга перемен как технология принятия решений")
    close = {"title": "Ицзин. Книга Перемен", "author": "", "translator": ""}
    far = {"title": "Технология оздоровительной физической культуры", "author": "", "translator": ""}
    assert base.matched(words, close) == 2
    assert base.matched(words, far) == 1


def test_author_found_by_surname(monkeypatch):
    """A surname is enough, and the shortest name that carries every word of the query wins."""
    seen = {}

    def fake_get(url, timeout=40):
        if "searchType=authors" in url:
            return AUTHORS_FEED.encode()
        seen["books"] = url
        return ENTRY.encode() if url.endswith("/alphabet") else b"<feed></feed>"

    monkeypatch.setattr("sources.opds.get", fake_get)
    name, books = Flibusta().author_books("виногродский")
    assert name == "Виногродский Бронислав Брониславович"
    assert seen["books"] == "https://flibusta.is/opds/author/2/alphabet"
    assert len(books) == 4


def test_search_falls_back_to_shorter_terms(monkeypatch):
    """The catalogs match a phrase inside a title: when the whole query names more than any title,
    shorter searches follow and rows that share too little with the query stay out."""
    asked = []

    class Catalog:
        name = "flibusta"

        def search(self, query):
            asked.append(query)
            rows = {
                "книга перемен": [base.hit(self.name, "Ицзин. Книга Перемен", "/b/1/fb2", "fb2")],
                "технология": [base.hit(self.name, "Технология сварки", "/b/2/fb2", "fb2")],
            }
            return rows.get(query, [])

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr("sources.SOURCES", [Catalog()])
    res = sources.search_text("Книга перемен как технология принятия решений")
    assert asked[0] == "Книга перемен как технология принятия решений"  # the phrase is tried first
    assert "книга перемен" in asked
    assert [r["title"] for r in res["hits"]] == ["Ицзин. Книга Перемен"]  # one shared word is not a match
    assert res["note"]


def test_two_word_query_needs_both_words(monkeypatch):
    """Two words are all a short query has: a row that names one of them is a coincidence."""

    class Catalog:
        name = "flibusta"

        def search(self, query):
            return (
                []
                if " " in query
                else [
                    base.hit(self.name, "Властелин колец", "/b/1/fb2", "fb2"),
                    base.hit(self.name, "Кольцо и роза", "/b/2/fb2", "fb2"),
                ]
            )

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr("sources.SOURCES", [Catalog()])
    res = sources.search_text("властелин колец")
    assert [r["title"] for r in res["hits"]] == ["Властелин колец"]


def test_word_matching_is_not_substring_matching():
    """«мир» must not be found inside «Владимир», but «войны» is still «война»."""
    words = base.terms("Толстой Война и мир")
    wrong = {"title": "1920. Война с белополяками", "author": "Меликов Владимир Арсеньевич", "translator": ""}
    right = {"title": "Война и мир", "author": "Толстой Лев Николаевич", "translator": ""}
    assert base.matched(words, wrong) == 1
    assert base.matched(words, right) == 3
    assert base.same_word("война", "войны") and not base.same_word("мир", "владимир")


def test_a_title_the_query_covers_outranks_a_book_about_it():
    words = base.terms("Толстой Война и мир")
    novel = {"title": "Война и мир", "author": "Толстой Лев Николаевич", "translator": ""}
    about = {"title": "Альбом акварелей к роману графа Л.Н. Толстого «Война и мир»", "author": "", "translator": ""}
    assert base.title_score("Толстой Война и мир", words, novel) > base.title_score("Толстой Война и мир", words, about)


def test_the_author_the_query_names_outranks_a_namesake_title():
    """«Виногродский книга перемен» wants his book, not somebody else's book of the same name."""
    q = "Виногродский книга перемен"
    words = base.terms(q)
    his = {"title": "Знаки Книги Перемен", "author": "Виногродский Бронислав Брониславович", "translator": ""}
    namesake = {"title": "Книга перемен", "author": "Вересов Дмитрий", "translator": ""}
    assert base.answers_whole_query(words, his)
    assert not base.answers_whole_query(words, namesake)
    assert base.title_score(q, words, his) > base.title_score(q, words, namesake)


def test_a_surname_alone_does_not_answer_a_query_about_a_title():
    """The bonus is for leaving nothing of the query unaccounted for, not for a name that half-fits."""
    words = base.terms("Дюна Герберт")
    other = {"title": "Стальная крыса", "author": "Гербертов Иван", "translator": ""}
    right = {"title": "Дюна", "author": "Герберт Фрэнк", "translator": ""}
    assert not base.answers_whole_query(words, other)
    assert base.answers_whole_query(words, right)
    assert base.title_score("Дюна Герберт", words, right) > base.title_score("Дюна Герберт", words, other)


def test_the_authors_own_shelf_answers_the_query_first(monkeypatch):
    """An author has dozens of books: the ones the query asks about come first, not the alphabet."""

    class Catalog:
        name = "flibusta"

        def search(self, query):
            return []

        def author_books(self, query):
            # alphabetically «Антология» leads; only the score can put «Книга Перемен» first
            return "Виногродский Бронислав Брониславович", [
                base.hit(self.name, "Антология даосской философии", "/b/1/fb2", "fb2", author="Виногродский Бронислав"),
                base.hit(self.name, "Знаки Книги Перемен", "/b/2/fb2", "fb2", author="Виногродский Бронислав"),
            ]

    sources.CACHE.clear()
    monkeypatch.setattr("sources.SOURCES", [Catalog()])
    res = sources.search_text("Виногродский книга перемен")
    assert [r["title"] for r in res["author"]["hits"]] == ["Знаки Книги Перемен", "Антология даосской философии"]


def test_a_book_only_in_a_format_we_cannot_open_is_counted(monkeypatch):
    """Nothing to show is not the same as nothing found: the card says the file is there but unreadable."""

    class Catalog:
        name = "flibusta"

        def search(self, query):
            return [base.hit(self.name, "Властелин колец", "/b/1/djvu", "djvu")]

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr("sources.SOURCES", [Catalog()])
    res = sources.search_text("властелин колец")
    assert res["hits"] == [] and res["unopenable"] == 1


def test_volume_numbers_do_not_share_a_cached_answer(monkeypatch):
    """«Книга 1» and «Книга 2» normalise to the same title, but they are different questions."""
    asked = []

    class Catalog:
        name = "flibusta"

        def search(self, query):
            asked.append(query)
            return []

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr("sources.SOURCES", [Catalog()])
    sources.search_text("Троецарствие, том 1")
    sources.search_text("Троецарствие, том 2")
    assert "Троецарствие, том 2" in asked


# --- open libraries: Standard Ebooks, Project Gutenberg, Wikisource, the Buddhadasa archive ---

SE_FEED = """<feed><entry>
<id>https://standardebooks.org/ebooks/laozi/tao-te-ching/james-legge</id>
<title>Tao Te Ching</title>
<author><name>Laozi</name><uri>https://standardebooks.org/ebooks/laozi</uri></author>
<dc:issued>2014-05-25T00:00:00Z</dc:issued><dc:language>en-GB</dc:language>
<link href="https://standardebooks.org/ebooks/laozi/tao-te-ching/james-legge/downloads/laozi_tao-te-ching_james-legge.epub?source=feed" length="803239" rel="http://opds-spec.org/acquisition/open-access" title="Recommended compatible epub" type="application/epub+zip" />
<link href="https://standardebooks.org/ebooks/laozi/tao-te-ching/james-legge/downloads/laozi_tao-te-ching_james-legge.azw3?source=feed" length="885471" rel="http://opds-spec.org/acquisition/open-access" title="Amazon Kindle azw3" type="application/x-mobipocket-ebook" />
</entry><entry>
<id>https://standardebooks.org/ebooks/w-b-yeats/poetry</id>
<title>Poetry</title><author><name>W. B. Yeats</name></author>
<link href="https://standardebooks.org/ebooks/w-b-yeats/poetry/downloads/w-b-yeats_poetry.epub?source=feed" length="1000" title="Recommended compatible epub" type="application/epub+zip" />
</entry></feed>"""


def test_standard_ebooks_entries(monkeypatch):
    from sources.standard_ebooks import StandardEbooks

    hits = StandardEbooks().entries(SE_FEED)
    tao = hits[0]
    assert tao["url"].endswith("laozi_tao-te-ching_james-legge.epub?source=feed") and tao["kind"] == "epub"
    assert (tao["author"], tao["translator"], tao["year"], tao["size_kb"], tao["lang"]) == (
        "Laozi",
        "James Legge",
        "2014",
        784,
        "en",
    )
    assert hits[1]["translator"] == ""
    # the search reads blurbs as well: a book that does not name the query is not a hit
    monkeypatch.setattr("sources.standard_ebooks.get", lambda url, timeout=40: SE_FEED.encode())
    assert [h["title"] for h in StandardEbooks().search("Tao Te Ching")] == ["Tao Te Ching"]


PG_FEED = """<feed><entry><id>https://www.gutenberg.org/ebooks/216.opds</id>
<title>The Tao Teh King, or the Tao and its Characteristics</title><content type="text">Laozi</content></entry>
<entry><id>https://www.gutenberg.org/ebooks/7337.opds</id><title>道德經 (Chinese)</title>
<content type="text">Laozi</content></entry>
<entry><id>https://www.gutenberg.org/ebooks/10471.opds</id>
<title>The World&#39;s Greatest Books — Volume 01</title><content type="text">1179 downloads</content></entry>
</feed>"""


def test_gutenberg_entries_and_spellings(monkeypatch):
    from sources.gutenberg import Gutenberg

    hits = Gutenberg().entries(PG_FEED)
    assert hits[0]["url"] == "https://www.gutenberg.org/ebooks/216.epub3.images" and hits[0]["kind"] == "epub"
    assert (hits[1]["title"], hits[1]["lang"]) == ("道德經", "zh")
    assert hits[2]["author"] == "" and hits[2]["title"] == "The World's Greatest Books — Volume 01"
    monkeypatch.setattr("sources.gutenberg.get", lambda url, timeout=40: PG_FEED.encode())
    # no row names «Tao Te Ching»: the catalog matched another spelling, so the rows sharing a word stand
    assert [h["title"] for h in Gutenberg().search("Tao Te Ching")] == [hits[0]["title"]]
    assert Gutenberg().search("The Web That Has No Weaver") == []  # found by subject, not one word shared
    # rows that name the query push out those the catalog found by subject
    assert [h["title"] for h in Gutenberg().search("Tao Teh King")] == [hits[0]["title"]]
    no_records = (
        "<feed><entry><id>https://www.gutenberg.org/ebooks.opds/</id><title>No records found.</title></entry></feed>"
    )
    assert Gutenberg().entries(no_records) == []


WS_ANSWER = {
    "query": {
        "pages": [
            {"index": 2, "title": "Война и мир (Толстой)", "pageprops": {}},
            {"index": 1, "title": "Война и мир", "pageprops": {"disambiguation": ""}},
            {"index": 3, "title": "Война и мир (Толстой)/Том 1", "pageprops": {}},
            {"index": 4, "title": "ЭСБЕ/Пекин", "pageprops": {}},
            {"index": 5, "title": "История Тибета и Хухунора", "pageprops": {}},
        ]
    }
}


def test_wikisource_keeps_whole_works(monkeypatch):
    import json

    from sources.wikisource import Wikisource, language

    assert language("Война и мир") == "ru" and language("Tao Te Ching") == "en"
    asked = []

    def fake_get(url, timeout=40):
        asked.append(url)
        return json.dumps(WS_ANSWER).encode()

    monkeypatch.setattr("sources.wikisource.get", fake_get)
    hits = Wikisource().search("Война и мир")
    assert asked[0].startswith("https://ru.wikisource.org/w/api.php?")
    assert [h["title"] for h in hits] == ["Война и мир (Толстой)"]  # no list of editions, chapter or article
    url = hits[0]["url"]
    assert url.startswith("https://ws-export.wmcloud.org/?lang=ru&format=epub-3&page=") and hits[0]["kind"] == "epub"
    assert "%D0%92%D0%BE%D0%B9%D0%BD%D0%B0_%D0%B8_%D0%BC%D0%B8%D1%80_(" in url


def test_accents_do_not_hide_a_title():
    from sources.standard_ebooks import relevant

    row = base.hit("wikisource", "Tâo Teh King", "u", "epub")
    assert relevant("tao teh king", [row]) == [row]
    assert relevant("ching", [base.hit("bia", "Dependent Quenching", "u", "pdf")]) == []  # whole words only


BIA_ANSWER = [
    {
        "title": {"rendered": "Handbook-for-Mankind-Buddhadasa-Bhikkhu"},
        "source_url": "https://main.bia.or.th/wp-content/uploads/2025/02/Handbook-for-Mankind-Buddhadasa-Bhikkhu.pdf",
        "media_details": {"filesize": 4661429},
    },
    {
        "title": {"rendered": "人類手冊-Handbook-for-Mankind-Buddhadasa-CN"},
        "source_url": "https://main.bia.or.th/wp-content/uploads/2025/02/x-CN.pdf",
        "media_details": {"filesize": 21807745},
    },
    {
        "title": {"rendered": "Руководство к жизни-Handbook for Mankind"},
        "source_url": "https://main.bia.or.th/wp-content/uploads/2025/02/ru.pdf",
        "media_details": {},
    },
    {"title": {"rendered": "Poster"}, "source_url": "https://main.bia.or.th/poster.jpg", "media_details": {}},
]


def test_bia_files_as_books(monkeypatch):
    import json

    from sources.bia import Bia

    hits = Bia().files(BIA_ANSWER)
    assert [(h["title"], h["author"], h["lang"], h["kind"]) for h in hits] == [
        ("Handbook for Mankind", "Buddhadasa Bhikkhu", "", "pdf"),
        ("人類手冊 Handbook for Mankind", "Buddhadasa Bhikkhu", "zh", "pdf"),
        ("Руководство к жизни Handbook for Mankind", "", "", "pdf"),
    ]
    assert hits[0]["size_kb"] == 4552 and hits[2]["size_kb"] is None
    monkeypatch.setattr("sources.bia.get", lambda url, timeout=40: json.dumps(BIA_ANSWER).encode())
    assert len(Bia().search("Руководство к жизни")) == 1


def test_open_libraries_follow_the_russian_catalogs():
    names = [s.name for s in sources.SOURCES]
    assert names[:3] == ["fantasy-worlds", "flibusta", "coollib"]
    assert {"standard-ebooks", "gutenberg", "wikisource", "bia"} <= set(names[3:])
    for s in sources.SOURCES:
        assert callable(s.search) and callable(s.author_books)
