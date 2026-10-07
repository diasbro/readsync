"""YouTube through yt-dlp: a search gives videos, `-g` gives a stream URL that expires (`expire=`).
yt-dlp always runs with --no-cache-dir, so neither a search nor a listen leaves files behind."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import urllib.parse

from .. import base

NAME = "youtube"
YTDLP = "yt-dlp"
SEARCH_N = 8
MIN_SECONDS = 300  # trailers and shorts are not audiobooks
BITRATE_KBPS = 129  # format 140, AAC: what a listen and a download get
FORMAT = "bestaudio[ext=m4a]/bestaudio"
SLOTS = threading.BoundedSemaphore(2)
TITLES: dict[str, tuple[str, float | None]] = {}  # what a search said about a video, for its parts list
STREAMS: dict[str, tuple[str, float]] = {}  # video id -> (stream URL, valid until)


def run(args: list[str], timeout: int = 40) -> str:
    with SLOTS:
        try:
            p = subprocess.run([YTDLP, "--no-cache-dir", *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("yt-dlp не найден") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"yt-dlp не ответил за {timeout} с") from None
    if p.returncode != 0:
        last = (p.stderr.strip().splitlines() or ["ошибка"])[-1]
        raise RuntimeError(f"yt-dlp: {last[:200]}")
    return p.stdout


def search(query: str) -> list[dict]:
    return parse_search(run(["--flat-playlist", "-J", "--", f"ytsearch{SEARCH_N}:{query} аудиокнига"]))


NARRATOR_RE = re.compile(
    r"(?:читает|читают|чтец|исполняет|озвучка|озвучил[аи]?)\s*[:\-–—]?\s*([^,.|()\[\]/]{3,60})", re.I
)


def parse_search(text: str) -> list[dict]:
    """Videos of a `--flat-playlist -J` search. There is no narrator field: it is taken from a title
    that says «читает …», else the channel stands in for it."""
    hits = []
    for e in json.loads(text).get("entries") or []:
        vid = str(e.get("id") or "")
        dur = e.get("duration")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid, re.ASCII) or not dur or dur < MIN_SECONDS:
            continue
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        title = str(e.get("title") or "")
        channel = str(e.get("channel") or e.get("uploader") or "")
        m = NARRATOR_RE.search(title)
        TITLES[vid] = (title, float(dur))
        hits.append(
            base.audio_hit(
                NAME,
                f"yt:{vid}",
                title,
                narrator=m.group(1).strip(" -–—") if m else channel,
                channel=channel,
                duration_s=int(dur),
                parts=1,
                size_bytes=int(dur * BITRATE_KBPS * 1000 / 8),
                bitrate_kbps=BITRATE_KBPS,
                captions=True,  # auto captions: anchors come from them, no speech recognition
                page_url=f"https://www.youtube.com/watch?v={vid}",
            )
        )
    return hits


def parts(ids: list[str]) -> list[dict]:
    """One part per video, in the order of the ref. The pipeline downloads `url` with yt-dlp."""
    out = []
    for i, vid in enumerate(ids):
        title, dur = TITLES.get(vid, (f"часть {i + 1}", None))
        out.append(
            {"title": title, "duration": dur, "url": f"https://www.youtube.com/watch?v={vid}", "size": None, "id": vid}
        )
    return out


def stream(vid: str) -> str:
    """A direct audio URL for a video, kept until a minute before it expires."""
    cached = STREAMS.get(vid)
    if cached and cached[1] > time.time():
        return cached[0]
    out = run(["-g", "-f", FORMAT, "--", f"https://www.youtube.com/watch?v={vid}"])
    url = next((line.strip() for line in out.splitlines() if line.strip()), "")
    if not url.startswith("https://"):
        raise RuntimeError("yt-dlp не дал ссылку на звук")
    expire = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("expire", [""])[0]
    until = int(expire) - 60 if expire.isdigit() else time.time() + 600
    STREAMS[vid] = (url, until)
    return url
