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
import tomllib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READER = ROOT / "reader"
BOOKS = ROOT / "books"
STATE_LOCK = threading.Lock()
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("application/json", ".json")


def list_books() -> list[dict]:
    out = []
    for toml in sorted(BOOKS.glob("*/book.toml")):
        try:
            meta = tomllib.loads(toml.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            meta = {"slug": toml.parent.name, "title": toml.parent.name, "error": str(e)}
        d = toml.parent
        meta["slug"] = d.name
        meta["ready"] = (d / "book.json").exists() and (d / "timing.json").exists()
        meta["audio"] = next((f.name for f in (d / "audio.m4a", d / "audio.mp3", d / "yt.webm") if f.exists()), None)
        if (d / "timing.json").exists():
            try:
                with (d / "timing.json").open("rb") as fh:
                    head = fh.read(200).decode("utf-8", "ignore")
                m = re.search(r'"source":\s*"(\w+)"', head)
                meta["timing_source"] = m.group(1) if m else "?"
            except OSError:
                pass
        out.append(meta)
    return out


def load_state(slug: str) -> dict:
    p = BOOKS / slug / "state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(slug: str, state: dict) -> None:
    p = BOOKS / slug / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def merge_state(slug: str, patch: dict) -> dict:
    """Last writer wins for position and settings (by client timestamp); stats days are merged by max."""
    with STATE_LOCK:
        st = load_state(slug)
        if "pos" in patch and patch.get("posAt", 0) >= st.get("posAt", 0):
            st["pos"], st["posAt"] = float(patch["pos"]), patch.get("posAt", 0)
        if "settings" in patch and patch.get("settingsAt", 0) >= st.get("settingsAt", 0):
            st["settings"], st["settingsAt"] = patch["settings"], patch.get("settingsAt", 0)
        if isinstance(patch.get("stats"), dict):
            days = st.setdefault("stats", {}).setdefault("days", {})
            for day, v in patch["stats"].get("days", {}).items():
                cur = days.get(day, {"sec": 0, "words": 0})
                days[day] = {"sec": max(cur["sec"], v.get("sec", 0)), "words": max(cur["words"], v.get("words", 0))}
        save_state(slug, st)
        return st


def add_session(slug: str, delta: dict) -> dict:
    with STATE_LOCK:
        st = load_state(slug)
        days = st.setdefault("stats", {}).setdefault("days", {})
        day = str(delta.get("day", ""))[:10]
        cur = days.get(day, {"sec": 0, "words": 0})
        days[day] = {
            "sec": cur["sec"] + float(delta.get("sec", 0)),
            "words": cur["words"] + float(delta.get("words", 0)),
        }
        save_state(slug, st)
        return st


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
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

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
        path = self.translate_path(self.path)
        if os.path.isfile(path) and "Range" in self.headers:
            return self.send_range(path)
        return super().do_GET()

    def do_PUT(self):
        slug, is_session = self.state_slug()
        if slug is None or is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        try:
            patch = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(merge_state(slug, patch))

    def do_POST(self):
        slug, is_session = self.state_slug()
        if slug is None or not is_session:
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        try:
            delta = self.read_json()
        except ValueError as e:
            return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(add_session(slug, delta))

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
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"readsync: http://{args.host}:{args.port}/  (books: {', '.join(b['slug'] for b in list_books()) or 'none'})")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()


if __name__ == "__main__":
    main()
