"""Text sources: OPDS parsing and the grouping of raw hits into editions. No network."""

from __future__ import annotations

import threading
import time

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


class Down:
    name = "down"

    def __init__(self):
        self.asked = []

    def search(self, query):
        self.asked.append(query)
        raise TimeoutError("The read operation timed out")

    def author_books(self, query):
        raise TimeoutError("The read operation timed out")


class Shelf:
    name = "shelf"

    def __init__(self):
        self.authors = []

    def search(self, query):
        return []

    def author_books(self, query):
        self.authors.append(query)
        if query == "торчинов":
            return "Торчинов Евгений", [
                base.hit("shelf", "Даосские практики", "https://x/b/1/fb2", "fb2", author="Торчинов Евгений")
            ]
        return "", []


def test_a_catalog_that_failed_is_not_asked_again_and_the_author_is_tried_at_both_ends(monkeypatch):
    """A catalog that is down gets its tries in the first round and sits the second out; «Даосские практики
    Торчинов» names its author last."""
    down, shelf = Down(), Shelf()
    monkeypatch.setattr(sources, "SOURCES", [down, shelf])
    monkeypatch.setattr(sources, "PAUSE", 0)
    sources.CACHE.clear()
    out = sources.search_text("Даосские практики Торчинов")
    assert down.asked == ["Даосские практики Торчинов"] * sources.TRIES
    assert "торчинов" in shelf.authors and "даосские" in shelf.authors
    assert out["author"]["name"] == "Торчинов Евгений"
    assert [h["title"] for h in out["author"]["hits"]] == ["Даосские практики"]


def test_a_search_called_off_stops_at_once(monkeypatch):
    """«отменить» ends the search between tries and does not wait for a hanging mirror."""
    import threading
    import time

    import pytest

    class Hang:
        name = "hang"

        def search(self, query):
            time.sleep(5)
            return []

        def author_books(self, query):
            time.sleep(5)
            return "", []

    monkeypatch.setattr(sources, "SOURCES", [Hang()])
    sources.CACHE.clear()
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(sources.Cancelled):
        sources.search_text("что угодно", cancel)
    assert time.monotonic() - t0 < 2


def test_a_round_that_ran_out_of_time_stops_its_tries(monkeypatch):
    """A mirror still failing at the deadline is not asked again in the background, minutes after the answer."""
    import threading
    import time

    calls = []

    class Flaky:
        name = "flaky"

        def search(self, query):
            calls.append(time.monotonic())
            raise TimeoutError("timed out")

        def author_books(self, query):
            return "", []

    monkeypatch.setattr(sources, "SOURCES", [Flaky()])
    monkeypatch.setattr(sources, "ROUND_SECONDS", 0.3)
    monkeypatch.setattr(sources, "PAUSE", 0.2)  # tries at 0, 0.2, 0.6, 1.2, 2.0 s if nothing stops them
    _, _, _, errors, _ = sources.ask(["что угодно"], cancel=threading.Event())
    asked = len(calls)
    time.sleep(1.2)
    assert errors == ["flaky: не ответил за 0.3 с"]
    assert len(calls) == asked


# ---------------------------------------------------------------- search rounds


def test_a_catalog_past_its_own_budget_does_not_hold_the_round(monkeypatch):
    """A catalog that names its own `seconds` is not waited for past it: the rows of the others are the
    answer, and the search does not hang on one mirror for the whole round."""
    gate = threading.Event()  # a request already in flight: nothing can call it back

    class Slow:
        name = "slow"
        seconds = 0.3

        def search(self, query):
            gate.wait(10)
            return []

        def author_books(self, query):
            return "", []

    class Quick:
        name = "quick"

        def search(self, query):
            return [base.hit("quick", "Книга перемен", "https://x/1.fb2", "fb2")]

        def author_books(self, query):
            return "", []

    monkeypatch.setattr(sources, "SOURCES", [Slow(), Quick()])
    monkeypatch.setattr(sources, "ROUND_SECONDS", 30)
    t0 = time.monotonic()
    try:
        hits, _, _, errors, failed = sources.ask(["книга перемен"], cancel=threading.Event())
        spent = time.monotonic() - t0
    finally:
        gate.set()  # the hanging request is let go: the test does not carry it into the next one
    assert spent < 2, f"ждали медленный каталог {spent:.1f} с"
    assert [h["title"] for h in hits] == ["Книга перемен"]  # the fast catalog answered while it hung
    assert errors == ["slow: не ответил за 0.3 с"] and failed == {"slow"}


def test_a_short_word_of_a_title_counts_for_a_match():
    """«Лунь юй» is a title of two two-letter words. The words a phrase search may ask for are the long
    ones, but a row that names the short ones too is that book and not a coincidence."""
    assert base.said_words("Лунь юй Виногродский") == ["лунь", "юй", "виногродский"]
    assert base.terms("Лунь юй Виногродский") == ["виногродский", "лунь"]  # what the catalogs are asked


def test_a_title_of_short_words_is_found_by_its_first_words(monkeypatch):
    class Catalog:
        name = "gutenberg"

        def search(self, query):
            if query == "лунь юй":
                return [base.hit(self.name, "Лунь Юй", "https://x/1.fb2", "fb2", author="Конфуций")]
            return []

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr(sources, "SOURCES", [Catalog()])
    res = sources.search_text("Лунь юй Виногродский")
    assert [r["title"] for r in res["hits"]] == ["Лунь Юй"]


def test_a_row_that_names_part_of_the_query_does_not_stop_the_shorter_searches(monkeypatch):
    """A row that names only some words of the query is not the book: the shorter searches still run."""

    class Partial:
        name = "wikisource"

        def search(self, query):
            return [base.hit(self.name, "ПУТЬ ДИСКУРСИВНОГО ПОЗНАНИЯ ТРАКТАТА", "https://x/article", "html")]

        def author_books(self, query):
            return "", []

    class Books:
        name = "gutenberg"

        def search(self, query):
            if query == "дао дэ цзин":
                return [base.hit(self.name, "Дао дэ цзин", "https://x/1.fb2", "fb2")]
            return []

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr(sources, "SOURCES", [Partial(), Books()])
    res = sources.search_text("Дао дэ цзин Виногродский")
    # the book the shorter search found comes first, and the first round's row is kept
    assert [r["title"] for r in res["hits"]] == ["Дао дэ цзин", "ПУТЬ ДИСКУРСИВНОГО ПОЗНАНИЯ ТРАКТАТА"]
    assert res["note"]


def test_a_row_that_names_the_whole_query_ends_the_search(monkeypatch):
    """The phrase found the book itself: no shorter searches follow, and the round is not asked twice."""
    asked = []

    class Catalog:
        name = "gutenberg"

        def search(self, query):
            asked.append(query)
            if query.lower() == "tao te ching":  # a catalog does not care for the case of a query
                return [base.hit(self.name, "Tao Te Ching", "https://x/1.epub", "epub", author="Laozi")]
            return []

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr(sources, "SOURCES", [Catalog()])
    res = sources.search_text("Tao Te Ching")
    assert [r["title"] for r in res["hits"]] == ["Tao Te Ching"] and res["note"] == ""
    assert asked == ["Tao Te Ching"]


def test_a_request_the_round_never_started_is_named_as_late(monkeypatch):
    """More requests than the pool has workers: a source whose requests were still queued when the round
    ended did not answer either, and is named so (and the answer is not cached as whole)."""

    class Slow:
        def __init__(self, name):
            self.name = name

        def search(self, query):
            time.sleep(0.3)
            return []

        def author_books(self, query):
            return "", []

    monkeypatch.setattr(sources, "SOURCES", [Slow(f"s{i}") for i in range(8)])
    monkeypatch.setattr(sources, "PER_SOURCE", 1)  # 8 workers for 8 * (3 + 2) = 40 requests
    monkeypatch.setattr(sources, "ROUND_SECONDS", 0.5)
    monkeypatch.setattr(sources, "PAUSE", 0)
    _, _, _, errors, failed = sources.ask(["а", "б", "в"], author_queries=("г", "д"), cancel=threading.Event())
    never_started = {f"s{i}" for i in range(4, 8)}  # 16 requests made in 0.5 s, these were behind them
    assert never_started <= failed, failed
    assert never_started <= {e.split(":")[0] for e in errors}, errors
    assert all(e.endswith("не ответил за 0.5 с") for e in errors), errors


def test_a_budget_runs_from_when_the_request_is_made(monkeypatch):
    """A request that waited in the queue behind others still gets its whole budget."""

    class Busy:
        name = "busy"

        def search(self, query):
            time.sleep(0.5)
            return []

        def author_books(self, query):
            time.sleep(0.5)
            return "", []

    class Quick:
        name = "quick"
        seconds = 0.3

        def search(self, query):
            return [base.hit("quick", "Книга перемен", "https://x/1.fb2", "fb2")]

        def author_books(self, query):
            return "", []

    monkeypatch.setattr(sources, "SOURCES", [Busy(), Quick()])
    monkeypatch.setattr(sources, "PER_SOURCE", 1)  # 2 workers, both taken by `busy` for 0.5 s
    monkeypatch.setattr(sources, "ROUND_SECONDS", 5)
    hits, _, _, errors, failed = sources.ask(["книга перемен"], author_queries=("x",), cancel=threading.Event())
    assert [h["title"] for h in hits] == ["Книга перемен"] and not errors and not failed


def test_initials_do_not_make_a_match():
    """«Л. Н. Толстой»: the initials are not words of the title, nor a search of their own."""
    assert base.said_words("Л. Н. Толстой Война и мир") == ["толстой", "война", "мир"]
    assert "л н" not in base.fallbacks("Л. Н. Толстой Война и мир")
    row = {"title": "Анна Каренина", "author": "Толстой Л. Н.", "translator": ""}
    assert base.matched(["л", "н", "толстой"], row) == 1


def test_the_note_is_shown_only_when_the_shorter_searches_found_something(monkeypatch):
    """The reader is told the list came from shorter searches when it did — and not when the first round
    already had the row and the shorter ones added nothing."""

    class Catalog:
        name = "gutenberg"

        def search(self, query):
            if query.lower() == "дао дэ цзин виногродский":  # only the query as typed
                return [base.hit(self.name, "Дао дэ цзин", "https://x/1.fb2", "fb2")]
            return []

        def author_books(self, query):
            return "", []

    sources.CACHE.clear()
    monkeypatch.setattr(sources, "SOURCES", [Catalog()])
    res = sources.search_text("Дао дэ цзин Виногродский")
    assert [r["title"] for r in res["hits"]] == ["Дао дэ цзин"] and res["note"] == ""


def test_a_catalog_that_declares_a_budget_declares_a_sane_one():
    """`ask` waits no longer than `ROUND_SECONDS` for anyone, so a budget past it would be a silent lie.
    A catalog that answers many pages at a time, or none at all when a mirror is down, has one."""
    for s in sources.SOURCES:
        assert 0 < getattr(s, "seconds", sources.ROUND_SECONDS) <= sources.ROUND_SECONDS, s.name
    assert Flibusta().seconds < sources.ROUND_SECONDS  # an OPDS shelf: several pages, and mirrors that go down
