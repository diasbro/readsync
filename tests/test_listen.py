"""The listen proxy against a fake source on 127.0.0.1: Range passes through, nothing reaches the disk,
the source is let go when the browser is, and only refs the server knows are followed. No network."""

from __future__ import annotations

import contextlib
import http.client
import os
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import serve
from sources import audio

DATA = bytes(i % 251 for i in range(1 << 20))  # 1 MB


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ranges: list[str] = []
    aborted: dict[str, threading.Event] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        name = self.path.strip("/")
        Upstream.ranges.append(self.headers.get("Range", ""))
        if name == "html":
            body = b"<html>checking your browser</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if name.startswith("slow"):  # an endless source: only a closed connection stops it
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            try:
                for _ in range(2000):
                    self.wfile.write(DATA[: 1 << 16])
                    time.sleep(0.01)
            except OSError:
                Upstream.aborted.setdefault(name, threading.Event()).set()
            self.close_connection = True
            return
        start, end = 0, len(DATA) - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, b = rng[6:].split("-")
            start, end = int(a), int(b) if b else len(DATA) - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self.wfile.write(DATA[start : end + 1])


def serve_in_thread(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


@pytest.fixture
def proxy(monkeypatch):
    up = serve_in_thread(Upstream)
    Upstream.ranges, Upstream.aborted = [], {}

    def fake_stream_url(ref, part):
        kind, args = audio.parse_ref(ref)  # the real gate: a ref the server does not know is a 400
        if kind != "ia":
            raise audio.BadRef("неизвестная озвучка")
        return f"http://127.0.0.1:{up.server_port}/{args[0]}", {}

    monkeypatch.setattr(audio, "stream_url", fake_stream_url)
    srv = serve_in_thread(serve.Handler)
    yield srv.server_port
    srv.shutdown()
    up.shutdown()


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def snapshot(root: Path) -> set:
    out = set()
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with contextlib.suppress(OSError):  # a file of another process may come and go
                out.add((p, os.path.getsize(p)))
    return out


def test_range_passes_through_and_nothing_is_written(proxy):
    roots = [serve.BOOKS, Path.home() / "Library" / "Caches" / "readsync", Path(tempfile.gettempdir())]
    before = [snapshot(r) for r in roots]
    r, body = get(proxy, "/api/audio/listen?ref=ia:range&part=0", {"Range": "bytes=1000-1999"})
    after = [snapshot(r) for r in roots]
    assert r.status == 206
    assert r.getheader("Content-Range") == f"bytes 1000-1999/{len(DATA)}"
    assert r.getheader("Content-Type") == "audio/mpeg"
    assert r.getheader("Cache-Control") == "no-store"
    assert body == DATA[1000:2000]
    assert Upstream.ranges == ["bytes=1000-1999"]
    for root, b, a in zip(roots, before, after, strict=True):
        assert a - b == set(), root


def test_client_going_away_closes_the_source(proxy):
    s = socket.create_connection(("127.0.0.1", proxy), timeout=10)
    s.sendall(b"GET /api/audio/listen?ref=ia:slow&part=0 HTTP/1.1\r\nHost: x\r\n\r\n")
    got = 0
    while got < 200_000:
        got += len(s.recv(65536))
    s.close()
    assert Upstream.aborted.setdefault("slow", threading.Event()).wait(10)


def test_another_recording_ends_the_one_playing(proxy):
    s = socket.create_connection(("127.0.0.1", proxy), timeout=10)
    s.sendall(b"GET /api/audio/listen?ref=ia:slow1&part=0 HTTP/1.1\r\nHost: x\r\n\r\n")
    s.recv(65536)
    t = threading.Thread(target=get, args=(proxy, "/api/audio/listen?ref=ia:range&part=0", {"Range": "bytes=0-9"}))
    t.start()
    t.join(10)
    assert Upstream.aborted.setdefault("slow1", threading.Event()).wait(10)
    s.close()


def test_a_page_instead_of_audio_is_a_502(proxy):
    r, body = get(proxy, "/api/audio/listen?ref=ia:html&part=0")
    assert r.status == 502
    assert "источник не отдаёт звук" in body.decode()


@pytest.mark.parametrize(
    "query",
    ["ref=https://evil.example/a.mp3&part=0", "ref=ia:../x&part=0", "ref=&part=0", "ref=ia:range&part=-1", "ref=yt:x"],
)
def test_unknown_ref_is_a_400(proxy, query):
    r, _ = get(proxy, "/api/audio/listen?" + query)
    assert r.status == 400
    assert Upstream.ranges == []  # nothing was asked of any source
