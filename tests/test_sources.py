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
