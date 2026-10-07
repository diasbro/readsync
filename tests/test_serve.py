"""The HTTP side of the server, against an in-process server on a free port. No network."""

from __future__ import annotations

import http.client
import json
import threading
import tracemalloc
import urllib.parse
from http.server import ThreadingHTTPServer

import pytest

import library
import serve
import state


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    for mod in (library, serve):
        monkeypatch.setattr(mod, "BOOKS", tmp_path)
    monkeypatch.setattr(library, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(library, "WISHLIST_FILE", tmp_path / "wishlist.json")
    d = tmp_path / "b"
    d.mkdir()
    (d / "book.toml").write_text('title = "B"\nedition = "e1"\n', encoding="utf-8")
    (d / "book.json").write_text('{"title": "B", "blocks": []}', encoding="utf-8")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def test_only_this_servers_own_pages_are_answered(server, tmp_path):
    """A page whose name was made to resolve to 127.0.0.1 names its own host; a page of another origin may
    send a form to any address. Neither reads the library nor changes it."""
    assert call(server, "GET", "/api/books", headers={"Host": f"evil.example:{server}"})[0] == 403
    assert call(server, "DELETE", "/api/books/b", headers={"Host": f"evil.example:{server}"})[0] == 403
    title = json.dumps({"title": "Чужое"}).encode()
    assert call(server, "POST", "/api/wishlist", title, {"Origin": "http://evil.example"})[0] == 403
    assert call(server, "POST", "/api/wishlist", title, {"Origin": "null"})[0] == 403
    assert library.load_wishlist() == [] and (tmp_path / "b").is_dir()
    assert call(server, "POST", "/api/wishlist", title, {"Origin": f"http://127.0.0.1:{server}"})[0] == 200
    assert call(server, "GET", "/api/books", headers={"Host": f"localhost:{server}"})[0] == 200


def test_a_body_that_is_not_an_object_is_refused_not_dropped(server):
    assert call(server, "PUT", "/api/books/b", b"[1]")[0] == 400
    status, body = call(server, "PUT", "/api/settings", b'{"settings": {"font": 20}, "settingsAt": "x"}')
    assert status == 200 and json.loads(body)["settings"] == {"font": 20}


def test_infinity_never_reaches_a_state_file(server, tmp_path):
    """NaN and Infinity are not JSON: JSON.parse and the phone would refuse the file that held one."""
    status, _ = call(server, "POST", "/api/state/b/session", b'{"day": "2026-10-07", "sec": "Infinity", "words": 1}')
    assert status == 200
    assert call(server, "PUT", "/api/state/b", b'{"pos": NaN, "posAt": 1}')[0] == 400
    raw = (tmp_path / "b" / "state" / ("a" * 32 + ".json")).read_text(encoding="utf-8")
    json.loads(raw, parse_constant=lambda c: pytest.fail(f"{c} in {raw}"))
    assert state.load(tmp_path / "b")["stats"]["days"]["2026-10-07"]["sec"] == 0


def test_a_picture_with_a_non_latin_name_is_served(server, tmp_path):
    (tmp_path / "b" / "images").mkdir()
    (tmp_path / "b" / "images" / "рис 1.jpg").write_bytes(b"\xff\xd8jpg")
    assert call(server, "GET", "/books/b/images/" + urllib.parse.quote("рис 1.jpg")) == (200, b"\xff\xd8jpg")
    assert call(server, "GET", "/books/" + urllib.parse.quote("../../etc/passwd"))[0] == 404
    assert call(server, "GET", "/books/b/%00")[0] == 404


def test_a_negative_length_is_refused_at_once(server):
    c = http.client.HTTPConnection("127.0.0.1", server, timeout=3)
    c.putrequest("POST", "/api/add")
    c.putheader("Content-Type", "multipart/form-data; boundary=x")
    c.putheader("Content-Length", "-1")
    c.endheaders()
    assert c.getresponse().status == 400  # read(-1) would have waited for the client to hang up
    c.close()


def _raw(port: int, request: bytes) -> list[str]:
    """The status lines the server sends back to raw bytes, until it closes the connection."""
    import re
    import socket

    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(request)
    data = b""
    try:
        while chunk := s.recv(65536):
            data += chunk
    except TimeoutError:
        pass
    s.close()
    return re.findall(r"HTTP/1\.[01] \d+", data.decode(errors="replace"))


@pytest.mark.parametrize("length", ["abc", "-5", "1e3"])
def test_a_length_that_is_not_a_byte_count_closes_the_connection(server, length):
    """Where the body ends is unknown: it is never read as the next request on the same connection."""
    host = f"Host: 127.0.0.1:{server}\r\n"
    inner = f"GET /api/settings HTTP/1.1\r\n{host}\r\n"
    for method in ("PUT /api/settings", "GET /api/books"):
        req = f"{method} HTTP/1.1\r\n{host}Content-Length: {length}\r\nContent-Type: application/json\r\n\r\n{inner}"
        assert _raw(server, req.encode()) == ["HTTP/1.1 400"]


def test_a_request_from_another_site_is_refused_a_get_too(server, monkeypatch):
    """An <img src> on any page would start a catalog search or end the listen stream. A page opened from
    elsewhere (the bookmarklet's /?wish=) is a top-level navigation and still opens."""
    seen = []
    monkeypatch.setattr(serve, "search_text", lambda q, c: seen.append(q) or [])
    img = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "image"}
    assert call(server, "GET", "/api/search?q=x", headers=img)[0] == 403
    assert call(server, "GET", "/api/search?q=x", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(server, "HEAD", "/api/books", headers={"Origin": "null"})[0] == 403
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert call(server, "GET", "/api/search?q=x", headers=nav)[0] == 403  # the API is no page
    assert call(server, "POST", "/api/wishlist", b"{}", {**nav, "Origin": "http://evil.example"})[0] == 403
    assert seen == []
    assert call(server, "GET", "/?wish=Kniga", headers=nav)[0] == 200
    ours = {"Sec-Fetch-Site": "same-origin", "Origin": f"http://127.0.0.1:{server}"}
    assert call(server, "GET", "/api/search?q=x", headers=ours)[0] == 200 and seen == ["x"]
    assert call(server, "GET", "/api/books")[0] == 200  # no fetch metadata at all: not a browser page


def test_a_rename_while_a_job_holds_the_book_is_a_conflict(server, tmp_path):
    library._pipeline()
    from tidy import claim, work_dir

    claim(work_dir("b"))
    status, body = call(server, "PUT", "/api/books/b", json.dumps({"title": "Новое"}).encode())
    assert status == 409 and "обрабатывается" in json.loads(body)["error"]
    assert 'title = "B"' in (tmp_path / "b" / "book.toml").read_text(encoding="utf-8")


def test_an_upload_is_held_in_memory_once():
    """A 600 MB audiobook used to cost several GB while the form was parsed; its fields still come out whole."""
    payload = bytes(range(256)) * (20 * 4096)  # 20 MB, every byte value, CRLFs included
    b = b"XyZbOuNdArY"
    body = b"".join(
        [
            b"--" + b + b'\r\nContent-Disposition: form-data; name="audio_file"; filename="\xd0\xb0.mp3"\r\n',
            b"Content-Type: audio/mpeg\r\n\r\n" + payload + b"\r\n",
            b"--" + b + b'\r\nContent-Disposition: form-data; name="audio_url"\r\n\r\nhttps://a/1\r\n',
            b"--" + b + b'\r\nContent-Disposition: form-data; name="audio_url"\r\n\r\nhttps://a/2\r\n',
            b"--"
            + b
            + b'\r\nContent-Disposition: form-data; name="title"\r\n\r\n \xd0\x9a\xd0\xbd\xd0\xb8\xd0\xb3\xd0\xb0 \r\n',
            b"--" + b + b"--\r\n",
        ]
    )
    tracemalloc.start()
    out = serve.parse_multipart(f"multipart/form-data; boundary={b.decode()}", body)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < len(payload) / 10
    assert out["audio_file"]["filename"] == "а.mp3" and bytes(out["audio_file"]["data"]) == payload
    assert out["audio_url"] == {"value": "https://a/1", "values": ["https://a/1", "https://a/2"]}
    assert out["title"] == {"value": "Книга"}
