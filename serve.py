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
import urllib.parse
import urllib.request
from email.parser import BytesParser
from email.policy import HTTP
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from library import (
    BOOKS,
    READER,
    SLUG_RE,
    STATE_LOCK,
    add_session,
    delete_book,
    job_status,
    list_books,
    load_settings,
    load_state,
    load_wishlist,
    merge_settings,
    merge_state,
    random_sentence,
    save_hits,
    start_align,
    start_job,
    where_now,
    wishlist_add,
    wishlist_delete,
    wishlist_update,
)
from sources import search_text

mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/json", ".json")


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """Return {field: {"value": str} | {"filename": str, "data": bytes}} from a multipart/form-data body."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    out: dict[str, dict] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fn = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if fn:
            out[name] = {"filename": fn, "data": payload}
        else:
            value = payload.decode("utf-8", "replace").strip()
            if name in out and "value" in out[name]:  # repeated field: keep every value (volumes / parts)
                out[name]["values"] = out[name].get("values", [out[name]["value"]]) + [value]
            else:
                out[name] = {"value": value}
    return out


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter log
        if args and "audio" in str(args[0]):
            return
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/books/"):
            rel = Path(path[len("/books/") :])
            target = (BOOKS / rel).resolve()
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

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            self.close_connection = True  # do not try to resync the stream after a refused body
            raise ValueError("body too large")
        raw = self.rfile.read(n) or b"{}"
        try:
            return json.loads(raw)
        except ValueError as e:
            raise ValueError(f"bad json: {e}") from None

    def read_form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 3_000_000_000:
            self.close_connection = True
            raise ValueError("body too large")
        return parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(n))

    def state_slug(self):
        m = re.match(r"^/api/state/([^/?]+)(/session)?$", self.path)
        if not m or not SLUG_RE.match(m.group(1)) or not (BOOKS / m.group(1)).is_dir():
            return None, None
        return m.group(1), bool(m.group(2))

    def do_GET(self):
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
                return self.send_json({"hits": [], "author_hits": None})
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
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0].strip()
            if not q:
                return self.send_json([])
            try:
                return self.send_json(search_text(q))
            except Exception as e:  # noqa: BLE001 - upstream site down or changed: report, don't crash
                return self.send_json({"error": f"поиск недоступен: {e}"}, HTTPStatus.BAD_GATEWAY)
        path = self.translate_path(self.path)
        if os.path.isfile(path) and "Range" in self.headers:
            return self.send_range(path)
        return super().do_GET()

    # The request body is always read before responding: on a keep-alive connection an unread
    # body would be parsed as the start of the next request.
    def do_PUT(self):
        try:
            body = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        if self.path.startswith("/api/settings"):
            return self.send_json(merge_settings(body))
        if self.path.startswith("/api/wishlist/"):
            return self.send_json(wishlist_update(self.path.rsplit("/", 1)[-1], body))
        if self.path.startswith("/api/hits/"):
            try:
                save_hits(self.path.rsplit("/", 1)[-1], body)
            except (ValueError, OSError) as e:
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return self.send_json({"ok": True})
        slug, is_session = self.state_slug()
        if slug is None or is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        return self.send_json(merge_state(slug, body))

    def do_POST(self):
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
        return self.send_json(add_session(slug, delta))

    def do_DELETE(self):
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
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"readsync: http://{args.host}:{args.port}/  (books: {', '.join(b['slug'] for b in list_books()) or 'none'})")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
