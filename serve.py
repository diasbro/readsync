#!/usr/bin/env python3
"""Local server for readsync: serves the reader UI, book data with HTTP Range support,
and /api/books (list of books/*/book.toml)."""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.parser import BytesParser
from email.policy import HTTP
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from library import (
    BOOKS,
    READER,
    SLUG_RE,
    STATE_LOCK,
    Busy,
    add_session,
    delete_book,
    ensure_manifests,
    job_status,
    list_books,
    load_settings,
    load_state,
    load_wishlist,
    merge_settings,
    merge_state,
    random_sentence,
    rename_book,
    save_hits,
    start_align,
    start_job,
    stop_job,
    where_now,
    wishlist_add,
    wishlist_delete,
    wishlist_update,
)
from sources import Cancelled, audio, search_text
from state import StateUnavailable

# searches in flight by the id the page gave them, so «отменить» can call one off on the server too
SEARCHES: dict[str, threading.Event] = {}
SEARCHES_LOCK = threading.Lock()

mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/json", ".json")

RANGE_RE = re.compile(r"bytes=[0-9]*-[0-9]*")
# the one live listen stream: a request for another recording ends the streams of the one before
LISTEN_LOCK = threading.Lock()
LISTEN = {"gen": 0, "ref": "", "since": 0}


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """An upstream may redirect (archive.org to its data nodes), but only to another https address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "redirect off https", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


UPSTREAM = urllib.request.build_opener(HttpsRedirects)


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """Return {field: {"value": str} | {"filename": str, "data": memoryview}} from a multipart/form-data body.
    Only the headers of each part go through the email parser; a file is a view into `body`, not a copy, so
    an audiobook upload is held in memory once."""
    boundary = (
        BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n").get_boundary()
    )
    if not boundary:
        raise ValueError("not a multipart form")
    delim = b"--" + boundary.encode("latin-1")
    view = memoryview(body)
    out: dict[str, dict] = {}
    at = body.find(delim)
    while at >= 0 and body[at + len(delim) : at + len(delim) + 2] != b"--":  # "--boundary--" closes the form
        head = at + len(delim)
        end = body.find(b"\r\n" + delim, head)
        split = body.find(b"\r\n\r\n", head, end)
        if end < 0 or split < 0:
            raise ValueError("cut-off multipart form")
        at = end + 2
        part = BytesParser(policy=HTTP).parsebytes(bytes(view[head:split]).lstrip(b" \t\r\n") + b"\r\n\r\n")
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fn = part.get_filename()
        payload = view[split + 4 : end]
        if fn:
            out[name] = {"filename": fn, "data": payload}
        else:
            value = bytes(payload).decode("utf-8", "replace").strip()
            if name in out and "value" in out[name]:  # repeated field: keep every value (volumes / parts)
                out[name]["values"] = out[name].get("values", [out[name]["value"]]) + [value]
            else:
                out[name] = {"value": value}
    return out


def no_constant(name: str) -> float:
    """NaN and Infinity are not JSON: saved, they would make a state file that JSON.parse refuses."""
    raise ValueError(f"{name} is not a number")


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter log
        if args and "audio" in str(args[0]):
            return
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def handle_error(self, *args):
        """A browser that walks away mid-download is not an error: seeking in an audiobook closes
        connections all the time, and a traceback per seek buries the log."""
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(*args)

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/books/"):
            try:  # the page asks for images/рис 1.jpg as images/%D1%80%D0%B8%D1%81%201.jpg
                target = (BOOKS / urllib.parse.unquote(path[len("/books/") :])).resolve()
            except (OSError, ValueError):  # a NUL byte, say
                return str(BOOKS / "__forbidden__")
            if BOOKS.resolve() in target.parents:
                return str(target)
            return str(BOOKS / "__forbidden__")
        rel = path.lstrip("/") or "index.html"
        if rel == "favicon.ico":  # browsers ask for it regardless of <link rel=icon>
            rel = "favicon.png"
        target = (READER / rel).resolve()
        if target == READER.resolve() or READER.resolve() in target.parents:
            return str(target)
        return str(READER / "__forbidden__")

    def send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def parse_request(self) -> bool:
        """A Content-Length that is not a number of bytes is refused before anything else: where its body ends
        is unknown, so the connection closes rather than read that body as the next request."""
        if not super().parse_request():
            return False
        n = (self.headers.get("Content-Length") or "").strip()
        if n and not re.fullmatch(r"[0-9]+", n):
            self.close_connection = True
            self.send_json({"error": "bad Content-Length"}, HTTPStatus.BAD_REQUEST)
            return False
        return True

    def body_length(self, limit: int) -> int:
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= limit:  # read(-1) would wait for the client to hang up
            self.close_connection = True  # do not try to resync the stream after a refused body
            raise ValueError("body too large" if n > limit else "bad Content-Length")
        return n

    def read_json(self) -> dict:
        """A JSON object; anything else (a list, NaN, Infinity) is the client's mistake."""
        raw = self.rfile.read(self.body_length(1_000_000)) or b"{}"
        try:
            body = json.loads(raw, parse_constant=no_constant)
        except ValueError as e:
            raise ValueError(f"bad json: {e}") from None
        if not isinstance(body, dict):
            raise ValueError("bad json: not an object")
        return body

    def read_form(self) -> dict:
        n = self.body_length(3_000_000_000)
        return parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(n))

    def refused(self) -> bool:
        """Only this server's own pages are answered: a request naming another host (a page whose name was
        made to resolve to 127.0.0.1) or one sent from another origin gets 403, a GET too (an <img> on any page
        would start a search or end a listen). A page opened from elsewhere (the «+ readsync» bookmarklet's
        /?wish=) is a plain top-level navigation to a page, not to the API, and is let through. True when refused."""
        host, port = self.server.server_address[:2]
        ours = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", host)}
        origin = self.headers.get("Origin")
        foreign = (origin is not None and origin not in {f"http://{o}" for o in ours}) or self.headers.get(
            "Sec-Fetch-Site"
        ) == "cross-site"
        navigation = (
            self.command in ("GET", "HEAD")
            and self.headers.get("Sec-Fetch-Mode") == "navigate"
            and self.headers.get("Sec-Fetch-Dest") == "document"
            and not self.path.startswith("/api/")
        )
        if self.headers.get("Host", f"127.0.0.1:{port}") in ours and (not foreign or navigation):
            return False
        self.close_connection = True  # its body, if any, is never read
        self.send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
        return True

    def state_slug(self):
        m = re.match(r"^/api/state/([^/?]+)(/session)?$", self.path)
        if not m or not SLUG_RE.match(m.group(1)) or not (BOOKS / m.group(1)).is_dir():
            return None, None
        return m.group(1), bool(m.group(2))

    def do_HEAD(self):
        if not self.refused():
            super().do_HEAD()

    def do_GET(self):
        if self.refused():
            return
        if self.path.startswith("/api/books"):
            return self.send_json(list_books())
        if self.path.startswith("/api/state/"):
            slug, _ = self.state_slug()
            if slug is None:
                return self.send_json({"error": "unknown book"}, HTTPStatus.NOT_FOUND)
            return self.send_json(load_state(slug))
        if self.path.startswith("/api/jobs"):
            return self.send_json(job_status())
        if self.path.startswith("/api/settings"):
            return self.send_json(load_settings())
        if self.path.startswith("/api/hits/"):
            slug = self.path.rsplit("/", 1)[-1]
            p = BOOKS / slug / "hits.json"
            if not SLUG_RE.match(slug) or not p.exists():
                return self.send_json({"hits": [], "author_hits": None, "query": ""})
            return self.send_json(json.loads(p.read_text(encoding="utf-8")))
        if self.path.startswith("/api/wishlist"):
            return self.send_json(load_wishlist())
        if self.path.startswith("/api/where/"):
            slug = self.path.rsplit("/", 1)[-1].split("?")[0]
            if not SLUG_RE.match(slug) or not (BOOKS / slug / "book.json").exists():
                return self.send_json({"error": "unknown book"}, HTTPStatus.NOT_FOUND)
            try:
                if "random=1" in self.path:
                    return self.send_json(random_sentence(slug))
                return self.send_json(where_now(slug))
            except Exception as e:  # noqa: BLE001
                return self.send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
        if self.path.startswith("/api/search"):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q = params.get("q", [""])[0].strip()
            sid = params.get("id", [""])[0][:64]
            if not q:
                return self.send_json([])
            cancel = threading.Event()
            if sid:
                with SEARCHES_LOCK:
                    SEARCHES[sid] = cancel
            try:
                return self.send_json(search_text(q, cancel))
            except Cancelled:
                return self.send_json({"error": "поиск отменён", "cancelled": True})
            except Exception as e:  # noqa: BLE001 - upstream site down or changed: report, don't crash
                return self.send_json({"error": f"поиск недоступен: {e}"}, HTTPStatus.BAD_GATEWAY)
            finally:
                if sid:
                    with SEARCHES_LOCK:
                        SEARCHES.pop(sid, None)
        if self.path.startswith("/api/audio/"):
            return self.send_audio()
        path = self.translate_path(self.path)
        if os.path.isfile(path) and "Range" in self.headers:
            return self.send_range(path)
        return super().do_GET()

    # The request body is always read before responding: on a keep-alive connection an unread
    # body would be parsed as the start of the next request.
    def do_PUT(self):
        if self.refused():
            return
        try:
            body = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/settings"):
            return self.send_json(merge_settings(body))
        if self.path.startswith("/api/wishlist/"):
            return self.send_json(wishlist_update(self.path.rsplit("/", 1)[-1], body))
        if self.path.startswith("/api/books/"):
            try:
                return self.send_json(rename_book(self.path.rsplit("/", 1)[-1], body.get("title", "")))
            except Busy as e:
                return self.send_json({"error": str(e)}, HTTPStatus.CONFLICT)
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/hits/"):
            try:
                save_hits(self.path.rsplit("/", 1)[-1], body)
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        slug, is_session = self.state_slug()
        if slug is None or is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        try:
            return self.send_json(merge_state(slug, body))
        except StateUnavailable as e:  # this device's file is not readable yet: try again, never overwrite it
            return self.send_json({"error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_POST(self):
        if self.refused():
            return
        if self.path.startswith("/api/add"):
            try:
                form = self.read_form()
                with STATE_LOCK:
                    job, err = start_job(form)
            except Exception as e:  # noqa: BLE001 - malformed multipart, bad paths, disk errors: report, don't crash
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            if err:
                return self.send_json({"error": err}, HTTPStatus.BAD_REQUEST)
            return self.send_json(job)
        try:
            delta = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/search/cancel"):
            with SEARCHES_LOCK:
                cancel = SEARCHES.get(str(delta.get("id", "")))
            if cancel:
                cancel.set()
            return self.send_json({"ok": bool(cancel)})
        if self.path.startswith("/api/align/"):
            with STATE_LOCK:
                job, err = start_align(self.path.rsplit("/", 1)[-1])
            return self.send_json(job or {"error": err}, HTTPStatus.OK if job else HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/wishlist"):
            try:
                return self.send_json(wishlist_add(delta))
            except ValueError as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        slug, is_session = self.state_slug()
        if slug is None or not is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        try:
            return self.send_json(add_session(slug, delta))
        except StateUnavailable as e:
            return self.send_json({"error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def do_DELETE(self):
        if self.refused():
            return
        if self.path.startswith("/api/jobs/"):
            try:
                with STATE_LOCK:
                    stop_job(self.path.rsplit("/", 1)[-1])
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        if self.path.startswith("/api/wishlist/"):
            return self.send_json(wishlist_delete(self.path.rsplit("/", 1)[-1]))
        if self.path.startswith("/api/books/"):
            try:
                delete_book(self.path.rsplit("/", 1)[-1])
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        if self.path.endswith((".json", ".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_audio(self):
        """Audio search, the parts of a recording, and listening to one. Nothing here touches the disk."""
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        ref = q.get("ref", [""])[0]
        try:
            if u.path == "/api/audio/search":
                text = q.get("q", [""])[0].strip()
                return self.send_json(audio.search(text) if text else {"hits": [], "errors": []})
            if u.path == "/api/audio/parts":
                return self.send_json([{"title": p["title"], "duration": p["duration"]} for p in audio.parts(ref)])
            if u.path == "/api/audio/listen":
                part = q.get("part", ["0"])[0]
                if not re.fullmatch(r"[0-9]{1,4}", part):
                    raise audio.BadRef("нет такой части")
                return self.send_listen(ref, int(part))
        except audio.BadRef as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:  # noqa: BLE001 - upstream site down or changed: report, don't crash
            return self.send_json({"error": f"источник недоступен: {e}"}, HTTPStatus.BAD_GATEWAY)
        return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def send_listen(self, ref: str, part: int) -> None:
        """Stream one part of a recording through to the browser, Range passed on both ways. Nothing is
        written to disk and the browser is told not to cache; when the browser goes away (a seek, a
        stop, a closed card) the upstream connection closes with it."""
        url, extra = audio.stream_url(ref, part)  # from the ref alone: the client never names a host
        headers = {"User-Agent": "Mozilla/5.0", **extra}  # no Referer: some hosts refuse 127.0.0.1
        if RANGE_RE.fullmatch(self.headers.get("Range", "")):
            headers["Range"] = self.headers["Range"]
        with LISTEN_LOCK:
            LISTEN["gen"] += 1
            gen = LISTEN["gen"]
            if LISTEN["ref"] != ref:
                LISTEN["ref"], LISTEN["since"] = ref, gen
        try:
            up = UPSTREAM.open(urllib.request.Request(url, headers=headers), timeout=20)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 416:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                if e.headers and e.headers.get("Content-Range"):
                    self.send_header("Content-Range", e.headers["Content-Range"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.send_json({"error": f"источник ответил {e.code}"}, HTTPStatus.BAD_GATEWAY)
        except (OSError, ValueError) as e:
            return self.send_json({"error": f"источник недоступен: {e}"}, HTTPStatus.BAD_GATEWAY)
        with up:
            if not (up.headers.get("Content-Type") or "").lower().startswith("audio/"):  # a captcha page, say
                return self.send_json({"error": "источник не отдаёт звук"}, HTTPStatus.BAD_GATEWAY)
            self.send_response(up.status)
            for h in ("Content-Type", "Content-Length", "Content-Range"):
                if up.headers.get(h):
                    self.send_header(h, up.headers[h])
            if not up.headers.get("Content-Length"):
                self.close_connection = True  # the end of the body is the end of the connection
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while LISTEN["since"] <= gen:  # another recording started: this one is over
                try:
                    chunk = up.read(1 << 16)
                    if not chunk:  # the end, or less than upstream promised: the connection closes below
                        break
                    self.wfile.write(chunk)
                except OSError:  # the browser left (BrokenPipe, reset) or the source stalled
                    break
            self.close_connection = True

    def send_range(self, path: str):
        size = os.path.getsize(path)
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers["Range"])
        if not m or not (m.group(1) or m.group(2)):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        ctype = self.guess_type(path)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8765))
    args = ap.parse_args()
    ensure_manifests()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"readsync: http://{args.host}:{args.port}/  (books: {', '.join(b['slug'] for b in list_books()) or 'none'})")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
