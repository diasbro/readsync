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
import subprocess
import sys
import threading
import time
import tomllib
from email.parser import BytesParser
from email.policy import HTTP
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READER = ROOT / "reader"
BOOKS = ROOT / "books"
STATE_LOCK = threading.Lock()
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
JOBS: dict[str, dict] = {}
PIPELINE_PY = ROOT / ".venv" / "bin" / "python"
TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a",
            "b",
            "v",
            "g",
            "d",
            "e",
            "e",
            "zh",
            "z",
            "i",
            "y",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "r",
            "s",
            "t",
            "u",
            "f",
            "h",
            "c",
            "ch",
            "sh",
            "sch",
            "",
            "y",
            "",
            "e",
            "yu",
            "ya",
        ],
        strict=True,
    )
)
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
        meta["ready"] = (d / "book.json").exists()
        meta["audio"] = next((f.name for f in (d / "audio.m4a", d / "audio.mp3", d / "yt.webm") if f.exists()), None)
        meta["has_audio"] = bool(meta["audio"]) and (d / "timing.json").exists()
        job = JOBS.get(d.name)
        meta["building"] = bool(job and job["proc"].poll() is None)
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
    """Last writer wins per key (by client timestamp in <key>At); stats days are merged by max."""
    with STATE_LOCK:
        st = load_state(slug)
        for key in ("pos", "sent", "mode", "settings"):
            if key in patch and patch.get(key + "At", 0) >= st.get(key + "At", 0):
                st[key], st[key + "At"] = patch[key], patch.get(key + "At", 0)
                if key == "sent" and "sentPct" in patch:
                    st["sentPct"] = patch["sentPct"]
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
        try:
            sec, words = float(delta.get("sec", 0)), float(delta.get("words", 0))
        except (TypeError, ValueError):
            sec, words = 0.0, 0.0
        cur = days.get(day, {"sec": 0, "words": 0})
        days[day] = {"sec": cur["sec"] + max(0.0, sec), "words": cur["words"] + max(0.0, words)}
        save_state(slug, st)
        return st


def slugify(title: str) -> str:
    s = "".join(TRANSLIT.get(c, c) for c in title.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:48]
    return s or "book"


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
        out[name] = {"filename": fn, "data": payload} if fn else {"value": payload.decode("utf-8", "replace").strip()}
    return out


def start_job(form: dict) -> tuple[dict | None, str]:
    """Save uploads, launch pipeline/add_book.py in the background. Returns (job info, error)."""
    val = lambda k: form.get(k, {}).get("value", "")  # noqa: E731
    title = val("title")
    slug = val("slug") or slugify(title or "book")
    if not SLUG_RE.match(slug):
        return None, "bad slug"
    d = BOOKS / slug
    if (d / "book.json").exists() or (slug in JOBS and JOBS[slug]["proc"].poll() is None):
        return None, f"книга {slug} уже есть"
    d.mkdir(parents=True, exist_ok=True)
    text = val("text_url")
    tf = form.get("text_file")
    if tf and tf.get("filename") and tf["data"]:
        ext = (
            ".fb2.zip"
            if tf["filename"].lower().endswith(".zip")
            else ".fb2"
            if tf["filename"].lower().endswith(".fb2")
            else ".html"
        )
        (d / ("upload" + ext)).write_bytes(tf["data"])
        text = str(d / ("upload" + ext))
    if not text:
        return None, "нужен текст: ссылка или файл"
    audio = val("audio_url")
    af = form.get("audio_file")
    if af and af.get("filename") and af["data"]:
        ext = os.path.splitext(af["filename"])[1].lower() or ".m4a"
        (d / ("upload" + ext)).write_bytes(af["data"])
        audio = str(d / ("upload" + ext))
    py = str(PIPELINE_PY) if PIPELINE_PY.exists() else sys.executable
    cmd = [py, str(ROOT / "pipeline" / "add_book.py"), slug, "--text", text]
    if audio:
        cmd += ["--audio", audio]
    if val("align") != "on":
        cmd.append("--no-align")
    for k, flag in (("title", "--title"), ("author", "--author"), ("narrator", "--narrator")):
        if val(k):
            cmd += [flag, val(k)]
    log = open(d / "add.log", "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    JOBS[slug] = {"proc": proc, "started": time.time(), "slug": slug}
    return {"slug": slug}, ""


def job_status() -> dict:
    out = {}
    for slug, j in JOBS.items():
        code = j["proc"].poll()
        try:
            log = (BOOKS / slug / "add.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        lines = [ln for ln in log.splitlines() if ln.strip() and "warning" not in ln.lower()]
        out[slug] = {"running": code is None, "exit": code, "log": lines[-6:], "started": j["started"]}
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

    def read_form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 3_000_000_000:
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
        if self.path.startswith("/api/add"):
            try:
                job, err = start_job(self.read_form())
            except Exception as e:  # noqa: BLE001 - malformed multipart, bad paths, disk errors: report, don't crash
                return self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            if err:
                return self.send_json({"error": err}, HTTPStatus.BAD_REQUEST)
            return self.send_json(job)
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
