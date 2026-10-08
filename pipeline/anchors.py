"""Coarse anchoring of book words to audio time using YouTube auto-captions (json3).

Produces anchors.json in the book directory:
{
  "words":   [[block_idx, char_start, char_end], ...],   # every word of narrated blocks, in order
  "anchors": [[word_idx, t_start, t_end], ...],           # confident matches (runs of >= MIN_RUN equal words)
  "coverage": float,
  "block_hits": [n_matched_words_per_block, ...]
}
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from bisect import bisect_left
from difflib import SequenceMatcher
from pathlib import Path

WORD_RE = re.compile(r"[\w]+(?:[-'’][\w]+)*", re.UNICODE)
MIN_RUN = 3
WINDOW = 1200
SLACK = 400
NGRAM = 4  # words of a rare phrase the place is found again by
LOST = 12  # a window matching fewer words than this is not where the captions are


def normalize(w: str) -> str:
    w = w.lower().replace("ё", "е").replace("’", "'")
    return re.sub(r"[^\w]", "", w)


def book_words(book: dict) -> tuple[list[list[int]], list[str]]:
    words: list[list[int]] = []
    norm: list[str] = []
    for bi, blk in enumerate(book["blocks"]):
        if not blk.get("audio", True):
            continue
        for m in WORD_RE.finditer(blk["text"]):
            n = normalize(m.group())
            if not n:
                continue
            words.append([bi, m.start(), m.end()])
            norm.append(n)
    return words, norm


def caption_words(json3: dict) -> tuple[list[float], list[float], list[str]]:
    starts: list[float] = []
    norm: list[str] = []
    for ev in json3["events"]:
        base = ev.get("tStartMs", 0)
        for seg in ev.get("segs", []):
            txt = seg.get("utf8", "")
            if not txt.strip():
                continue
            t = (base + seg.get("tOffsetMs", 0)) / 1000.0
            for tok in txt.split():
                n = normalize(tok)
                if n:
                    starts.append(t)
                    norm.append(n)
    ends = starts[1:] + [starts[-1] + 0.5 if starts else 0.0]
    # a caption word cannot last longer than ~2s (gaps between events)
    ends = [min(e, s + 2.0) for s, e in zip(starts, ends, strict=False)]
    return starts, ends, norm


def rare_ngrams(a: list[str]) -> dict[tuple[str, ...], int]:
    """Every NGRAM words that occur once in the book, with where: a place the captions can be found at."""
    seen: dict[tuple[str, ...], int] = {}
    for k in range(len(a) - NGRAM + 1):
        g = tuple(a[k : k + NGRAM])
        seen[g] = -1 if g in seen else k
    return {g: k for g, k in seen.items() if k >= 0}


def chain(rare: dict[tuple[str, ...], int], b: list[str]) -> list[tuple[int, int]]:
    """Where the book's rare n-grams are said, (book, caption), keeping the longest run in order on both (patience
    sorting, as reextract's match_words): the reading itself. A passage said out of its place (a quote in an
    intro, the annotation the book keeps at its end read first) is a short run and falls out of it."""
    hits = []
    for jj in range(len(b) - NGRAM + 1):
        ii = rare.get(tuple(b[jj : jj + NGRAM]))
        if ii is not None:
            hits.append((ii, jj))
    tails: list[int] = []  # the smallest book place ending an increasing run of each length
    ends: list[int] = []
    prev: list[int | None] = [None] * len(hits)
    for k, (ii, _) in enumerate(hits):
        p = bisect_left(tails, ii)
        if p == len(tails):
            tails.append(ii)
            ends.append(k)
        else:
            tails[p], ends[p] = ii, k
        prev[k] = ends[p - 1] if p else None
    out = []
    k = ends[-1] if ends else None
    while k is not None:
        out.append(hits[k])
        k = prev[k]
    return out[::-1]


def chunked_match(a: list[str], b: list[str]) -> list[tuple[int, int, int]]:
    """Monotonic matching blocks (i, j, n) between long sequences a and b via sliding windows. A window that
    finds (next to) nothing has lost the place, as when the captions begin past the window (a second volume, a
    preface not read, a part without captions) or skip a chapter: it goes on from the next point of the chain of
    the book's rare n-grams in the captions, at any distance ahead."""
    i = j = 0
    out: list[tuple[int, int, int]] = []
    points: list[tuple[int, int]] | None = None
    while i < len(a) and j < len(b):
        wa = a[i : i + WINDOW]
        wb = b[j : j + WINDOW + SLACK]
        sm = SequenceMatcher(None, wa, wb, autojunk=False)
        blocks = [bl for bl in sm.get_matching_blocks() if bl.size >= MIN_RUN]
        if sum(bl.size for bl in blocks) < min(LOST, len(wb) // 2):  # a few captions left: half of them will do
            if points is None:
                points = chain(rare_ngrams(a), b)
                at_book, at_caps = [p[0] for p in points], [p[1] for p in points]
            k = max(bisect_left(at_caps, j), bisect_left(at_book, i))  # the next point ahead on both sides
            if k < len(points) and points[k] == (i, j):  # lost right at it: a point off the reading
                k += 1
            if k < len(points):
                i, j = points[k]
                continue
        if not blocks:
            # no confident match in this window: skip ahead conservatively
            i += WINDOW // 2
            j += WINDOW // 2
            continue
        commit_limit = int(len(wa) * 0.7) if i + WINDOW < len(a) else len(wa)
        committed = [bl for bl in blocks if bl.a + bl.size <= commit_limit] or blocks[:1]
        for bl in committed:
            out.append((i + bl.a, j + bl.b, bl.size))
        last = committed[-1]
        ni, nj = i + last.a + last.size, j + last.b + last.size
        if ni <= i:  # safety against stalls
            ni, nj = i + WINDOW // 2, j + WINDOW // 2
        i, j = ni, nj
    return out


def audio_seconds(*dirs: Path) -> float:
    """The length of the first playable audio found in `dirs`, 0 when there is none or ffprobe cannot tell."""
    src = next((x / n for x in dirs for n in ("audio.m4a", "audio.mp3", "yt.webm") if (x / n).exists()), None)
    if src is None:
        return 0.0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return float(out or 0)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0.0


def build(book: dict, json3: dict, audio_sec: float = 0.0) -> dict:
    """`audio_sec`: the recording's own length. Captions may stop before it does (a part without them,
    closing music), and the words after the last one are spread up to the real end, not squeezed into it."""
    words, bnorm = book_words(book)
    cstart, cend, cnorm = caption_words(json3)
    matches = chunked_match(bnorm, cnorm)
    anchors: list[list[float]] = []
    hits = [0] * len(book["blocks"])
    for ai, bj, n in matches:
        for k in range(n):
            wi = ai + k
            anchors.append([wi, round(cstart[bj + k], 3), round(cend[bj + k], 3)])
            hits[words[wi][0]] += 1
    anchors.sort()
    # drop non-monotonic anchors (time must increase with word index)
    mono: list[list[float]] = []
    for a in anchors:
        if not mono or a[1] >= mono[-1][1]:
            mono.append(a)
    coverage = len(mono) / max(1, len(words))
    return {
        "words": words,
        "anchors": mono,
        "coverage": round(coverage, 4),
        # how much of what the recording says is the book's text: a recording of part of the book (one volume,
        # an abridged reading, captions on some parts only) still matches well; another translation does not
        "spoken": len(cnorm),
        "matched": len(mono),
        "match": round(len(mono) / max(1, len(cnorm)), 4),
        "block_hits": hits,
        "duration": max(cend[-1] if cend else 0.0, audio_sec),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    ap.add_argument(
        "--work", type=Path, help="where anchors.json goes and a new text and captions are looked for first"
    )
    args = ap.parse_args()
    d = args.book_dir
    w = args.work or d
    text = w / "book.json" if (w / "book.json").exists() else d / "book.json"  # a new text waits in the work dir
    book = json.loads(text.read_text(encoding="utf-8"))
    caps = next(
        (f for x in (w, d) for f in sorted(x.glob("yt.*.json3")) + [x / "whisper.json3"] if f.exists()), None
    )  # yt.merged / yt.ru-orig
    if caps is None:
        sys.exit("no *.json3 captions in book dir")
    res = build(book, json.loads(caps.read_text(encoding="utf-8")), audio_seconds(w, d))
    (w / "anchors.json").write_text(json.dumps(res), encoding="utf-8")
    words_per_block = {}
    for bi, _, _ in res["words"]:
        words_per_block[bi] = words_per_block.get(bi, 0) + 1
    silent = [bi for bi, n in words_per_block.items() if n >= 6 and res["block_hits"][bi] == 0]
    print(
        f"book words={len(res['words'])} anchors={len(res['anchors'])} coverage={res['coverage']:.1%} "
        f"spoken={res['spoken']} match={res['match']:.1%} "
        f"blocks_without_hits(>=6 words)={len(silent)}"
    )
    gaps = []
    prev_t, prev_w = 0.0, 0
    for wi, t0, _ in res["anchors"]:
        if t0 - prev_t > 60:
            gaps.append((round(prev_t), round(t0), wi - prev_w))
        prev_t, prev_w = t0, wi
    print("audio gaps > 60s without anchors:", gaps[:20], "..." if len(gaps) > 20 else "")


if __name__ == "__main__":
    main()
