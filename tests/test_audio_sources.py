"""Audio sources: search rows, parts and refs, from hand-written fragments. No network."""

from __future__ import annotations

import json
import threading
import time

import pytest

from sources import audio
from sources.audio import archive, knigavuhe, youtube

SEARCH_PAGE = """<html><body><div class="books_list">
<div class="bookkitem">
  <a href="/book/puteshestvie-na-zapad-1/" class="bookkitem_cover"><img class="bookkitem_cover_img" src="https://s5.knigavuhe.org/1/covers/50486/1-1.jpg?1"></a>
  <div class="bookkitem_name"><a class="bookkitem_name" href="/book/puteshestvie-na-zapad-1/"><span class="highlight_keyword">Путешествие</span> <span class="highlight_keyword">на</span> <span class="highlight_keyword">Зап</span>ад</a>
  <span class="bookkitem_author"><span class="bookkitem_author_label">автор</span> <a href="/author/u-chehnehn/">У Чэнъэнь</a></span></div>
  <div class="bookkitem_meta">
    <div class="bookkitem_meta_block"><span class="bookkitem_icon -reader"></span> <span class="bookkitem_meta_label">Читает</span> <a href="/reader/kir-dmitriev/">Кир Дмитриев</a></div>
    <div class="bookkitem_meta_block"><span class="bookkitem_icon -time"></span> <span class="bookkitem_meta_time">97 часов 37 минут</span></div>
  </div>
</div>
<div class="bookkitem">
  <a href="/book/iphuck-10-1/" class="bookkitem_name">iPhuck 10</a>
  <span class="bookkitem_author"><a href="/author/x/">Автор</a></span>
  <span class="bookkitem_meta_block -reader">Читают: <a href="/reader/a/">Первый</a>, <a href="/reader/b/">Второй</a></span>
  <div class="bookkitem_meta_block"><span class="bookkitem_icon -time"></span> <span class="bookkitem_meta_time">11 часов 2 минуты</span></div>
  <span class="bookkitem_meta_block -litres">ЛитРес</span>
</div>
<div class="bookkitem"><a href="/book/../../etc/" class="bookkitem_name">Чужая ссылка</a></div>
</div></body></html>"""

PLAYER_PAGE = """<script>var player = new BookPlayer(50486, [
{"id":1,"title":"001","url":"https:\\/\\/s5.knigavuhe.org\\/1\\/audio\\/50486\\/0123456789abcdef0123456789abcdef\\/p-001.mp3","duration":3463,"duration_float":3463.4,"url_refresh_in":277200},
{"id":2,"title":"002","url":"https://s5.knigavuhe.org/1/audio/50486/0123456789abcdef0123456789abcdef/p-002.mp3","duration":3500},
{"id":3,"title":"чужое","url":"https://example.com/p-003.mp3","duration":10}
], [1, 1.5, 2], {"blocked":false});</script>"""

TRIAL_PAGE = """new BookPlayer(777, [{"id":9,"title":"Фрагмент","url":"https://www.litres.ru/audiotrial/?art=1","duration":0}], []);"""

ADVANCED = {
    "response": {
        "docs": [
            {
                "identifier": "puteshestvie_librivox",
                "title": "Путешествие",
                "creator": ["Автор"],
                "collection": ["librivoxaudio"],
            },
            {"identifier": "../evil", "title": "x"},
            {"identifier": "no_mp3_item", "title": "y"},
        ]
    }
}
METADATA = {
    "metadata": {
        "title": "Путешествие на Запад",
        "creator": "У Чэнъэнь",
        "collection": ["librivoxaudio", "audio_bookspoetry"],
    },
    "files": [
        {"name": "part_10.mp3", "format": "VBR MP3", "length": "100.5", "size": "2000000"},
        {"name": "part_2.mp3", "format": "VBR MP3", "length": "01:40", "size": "1000000", "title": "Глава 2"},
        {"name": "part_2_64kb.mp3", "format": "64Kbps MP3", "length": "100", "size": "800000"},
        {"name": "cover.jpg", "format": "JPEG", "size": "1000"},
    ],
}

YTSEARCH = {
    "entries": [
        {
            "id": "AbCdEfGhI_1",
            "title": "Путешествие на Запад. Часть 1. Читает Иван Петров",
            "channel": "Канал",
            "duration": 27000,
        },
        {
            "id": "ZyXwVuTsR-2",
            "title": "Путешествие на Запад, аудиокнига",
            "channel": "Другой канал",
            "duration": 3600.0,
        },
        {"id": "short000001", "title": "трейлер", "channel": "c", "duration": 59},
        {"id": "bad id", "title": "x", "duration": 5000},
    ]
}


def test_knigavuhe_search_row_fields():
    rows = knigavuhe.parse_search(SEARCH_PAGE)
    assert len(rows) == 2  # the row whose link leaves /book/<slug>/ is not a row
    r = rows[0]
    assert r["ref"] == "knigavuhe:50486:puteshestvie-na-zapad-1"
    assert (r["title"], r["author"], r["narrator"]) == ("Путешествие на Запад", "У Чэнъэнь", "Кир Дмитриев")
    assert r["duration_s"] == 351420
    assert r["size_bytes"] == 351420 * 16000 and r["bitrate_kbps"] == 128
    assert r["page_url"] == "https://knigavuhe.org/book/puteshestvie-na-zapad-1/"
    assert not r["licensed_trial"] and not r["captions"]
    assert rows[1]["narrator"] == "Первый, Второй"
    assert rows[1]["licensed_trial"]
    assert rows[1]["ref"] == "knigavuhe:0:iphuck-10-1"  # no cover, no id: the slug alone finds the page


def test_duration_forms():
    assert knigavuhe.duration_of("97 часов 37 минут") == 351420
    assert knigavuhe.duration_of("1 час 5 минут 3 секунды") == 3903
    assert knigavuhe.duration_of("2 ч 1 мин") == 7260
    assert knigavuhe.duration_of("") is None


def test_book_player_gives_parts_on_own_hosts_only():
    parts = knigavuhe.parse_player(PLAYER_PAGE, 50486)
    assert [p["title"] for p in parts] == ["001", "002"]
    assert parts[0]["duration"] == 3463.4 and parts[1]["duration"] == 3500
    assert parts[0]["url"].startswith("https://s5.knigavuhe.org/1/audio/50486/")
    with pytest.raises(RuntimeError):
        knigavuhe.parse_player(PLAYER_PAGE, 11)  # the page is of another book


def test_litres_trial_is_no_recording(monkeypatch):
    with pytest.raises(knigavuhe.TrialOnly):
        knigavuhe.parse_player(TRIAL_PAGE, 777)
    monkeypatch.setattr(audio, "CACHE", {})
    monkeypatch.setattr(audio, "SOURCES", [("knigavuhe", lambda q: knigavuhe.parse_search(SEARCH_PAGE))])
    out = audio.search("путешествие")
    assert [h["ref"] for h in out["hits"]] == ["knigavuhe:50486:puteshestvie-na-zapad-1"]
    assert out["errors"] == []


def test_archive_search_and_metadata(monkeypatch):
    assert archive.lucene("Путешествие на Запад") == (
        "mediatype:audio AND (title:(путешествие) OR creator:(путешествие)) AND (title:(запад) OR creator:(запад))"
    )
    docs = archive.parse_search(ADVANCED)
    assert [d["identifier"] for d in docs] == ["puteshestvie_librivox", "no_mp3_item"]
    parts = archive.parse_metadata("puteshestvie_librivox", METADATA)
    assert [p["url"] for p in parts] == [
        "https://archive.org/download/puteshestvie_librivox/part_2.mp3",
        "https://archive.org/download/puteshestvie_librivox/part_10.mp3",
    ]  # one format (VBR first), in natural order
    assert parts[0]["title"] == "Глава 2" and parts[0]["duration"] == 100.0 and parts[1]["size"] == 2000000

    metas = {"puteshestvie_librivox": METADATA, "no_mp3_item": {"files": [{"name": "a.ogg", "format": "Ogg"}]}}

    def fake_fetch(url):
        if "advancedsearch" in url:
            return ADVANCED
        return metas[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(archive, "fetch", fake_fetch)
    hits = archive.search("Путешествие на Запад")
    assert len(hits) == 1
    h = hits[0]
    assert h["ref"] == "ia:puteshestvie_librivox" and h["parts"] == 2
    assert h["size_bytes"] == 3000000 and h["duration_s"] == 200 and h["bitrate_kbps"] == 120
    assert (
        h["author"] == "У Чэнъэнь"
        and h["librivox"]
        and h["page_url"] == "https://archive.org/details/puteshestvie_librivox"
    )


def test_youtube_search_rows():
    rows = youtube.parse_search(json.dumps(YTSEARCH))
    assert [r["ref"] for r in rows] == ["yt:AbCdEfGhI_1", "yt:ZyXwVuTsR-2"]
    assert rows[0]["narrator"] == "Иван Петров"
    assert rows[1]["narrator"] == "Другой канал"  # no «читает» in the title: the channel stands in
    assert rows[0]["captions"] and rows[0]["duration_s"] == 27000 and rows[0]["parts"] == 1
    parts = youtube.parts(["AbCdEfGhI_1", "unknownvid1"])
    assert parts[0]["title"].startswith("Путешествие") and parts[1]["title"] == "часть 2"
    assert parts[1]["url"] == "https://www.youtube.com/watch?v=unknownvid1"


def test_youtube_stream_url_is_cached_until_it_expires(monkeypatch):
    calls = []

    def fake_run(args, timeout=40):
        calls.append(args)
        return f"https://rr1.googlevideo.com/videoplayback?expire={int(time.time()) + 3600}&id=1\n"

    monkeypatch.setattr(youtube, "run", fake_run)
    monkeypatch.setattr(youtube, "STREAMS", {})
    monkeypatch.setattr(audio, "PARTS", {})
    url, headers = audio.stream_url("yt:AbCdEfGhI_1", 0)
    assert url.startswith("https://rr1.googlevideo.com/") and headers == {}
    audio.stream_url("yt:AbCdEfGhI_1", 0)
    assert len(calls) == 1
    assert calls[0][:3] == ["-g", "-f", youtube.FORMAT] and calls[0][-1].endswith("v=AbCdEfGhI_1")
    with pytest.raises(audio.BadRef):
        audio.stream_url("yt:AbCdEfGhI_1", 1)  # one video, one part


@pytest.mark.parametrize(
    "ref, kind, args",
    [
        ("knigavuhe:50486:puteshestvie-na-zapad-1", "knigavuhe", (50486, "puteshestvie-na-zapad-1")),
        ("yt:AbCdEfGhI_1,ZyXwVuTsR-2", "yt", (["AbCdEfGhI_1", "ZyXwVuTsR-2"],)),
        ("ia:puteshestvie_librivox.v2", "ia", ("puteshestvie_librivox.v2",)),
    ],
)
def test_ref_parses(ref, kind, args):
    assert audio.parse_ref(ref) == (kind, args)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "https://evil.example/a.mp3",
        "knigavuhe:1:../../etc",
        "knigavuhe:1:slug/..",
        "knigavuhe:x:slug",
        "knigavuhe:1:slug\n",
        "knigavuhe:١:slug",  # a digit, but not an ASCII one
        "yt:short",
        "yt:AbCdEfGhI_1,",
        "ia:../x",
        "ia:a/b",
        "ia:evil.example@x",
        "http:x",
        None,
    ],
)
def test_ref_refuses_anything_else(ref):
    with pytest.raises(audio.BadRef):
        audio.parse_ref(ref)


def test_urls_come_from_the_ref(monkeypatch):
    """The page fetched for a knigavuhe ref and the item for an ia ref are on the source's own host."""
    seen = []
    monkeypatch.setattr(knigavuhe, "fetch", lambda url: seen.append(url) or PLAYER_PAGE)
    monkeypatch.setattr(archive, "fetch", lambda url: seen.append(url) or METADATA)
    monkeypatch.setattr(audio, "PARTS", {})
    url, _ = audio.stream_url("knigavuhe:50486:puteshestvie-na-zapad-1", 1)
    assert url.endswith("/p-002.mp3")
    url, _ = audio.stream_url("ia:puteshestvie_librivox", 0)
    assert url == "https://archive.org/download/puteshestvie_librivox/part_2.mp3"
    assert seen == [
        "https://knigavuhe.org/book/puteshestvie-na-zapad-1/",
        "https://archive.org/metadata/puteshestvie_librivox",
    ]
    audio.parts("ia:puteshestvie_librivox")
    assert len(seen) == 2  # parts are cached


def test_search_keeps_a_deadline_and_reports_failures(monkeypatch):
    release = threading.Event()

    def slow(q):
        release.wait(5)
        return []

    def broken(q):
        raise RuntimeError("HTTP Error 503")

    monkeypatch.setattr(audio, "CACHE", {})
    monkeypatch.setattr(audio, "ROUND_SECONDS", 0.3)
    monkeypatch.setattr(audio, "SOURCES", [("a", lambda q: [{"ref": "ia:x"}]), ("b", slow), ("c", broken)])
    t = time.monotonic()
    out = audio.search("что-нибудь")
    release.set()
    assert time.monotonic() - t < 2
    assert out["hits"] == [{"ref": "ia:x"}]
    assert out["errors"] == ["b: не ответил за 0.3 с", "c: HTTP Error 503"]
    assert audio.CACHE == {}  # a failed round is worth retrying
