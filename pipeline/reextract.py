#!/usr/bin/env python3
"""Extract the text of a book already in the library again, the way add_book did (after the extractors
were fixed), and keep what was read in it.

  python pipeline/reextract.py <book_dir> [--source FILE_OR_URL ...] [--dry-run]

The sources are book.toml's `text_source` (parts joined by " | ") unless --source names others. The new
text is built in the job's work dir and lands in one step per file; book.toml is stamped last and the
reading state (state/) is never touched.

- Every sentence reads as before: the edition stays (only the sizes in `files` change).
- Otherwise a new edition, and `editions.json` maps every sentence index of the old edition to the new
  sentence holding the old sentence's first word (word sequences aligned in order), composed with the
  maps already there: `{"edition": <the new edition>, "maps": {<old edition>: [...], ...}}`. state.py and
  the phone carry page positions over through it while the book's edition is the one it names. A run cut
  short after the map landed but before the stamp is finished by the next one: the edition it named is
  stamped once the text in the book is the one extracted.
- A book with audio: timing.json points at blocks and characters, so when the narrated words moved it
  is built again as add_book builds it, from the captions kept in the book (yt.*.json3) and its audio,
  then aligned by MMS when that was the book's timing. Without what that needs, the book is refused and
  left as it was.

--dry-run builds and compares, prints what would change and writes nothing into the book.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tomllib
import uuid
from bisect import bisect_left
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from add_book import PIPE, PY, build_text, fragment_note, is_url, run  # noqa: E402
from anchors import WORD_RE, book_words, normalize  # noqa: E402
from manifest import _set, clean_title, stamp  # noqa: E402
from tidy import PLAYABLE, land, take, tidy, work_dir  # noqa: E402

SMALL = 250_000  # a stretch is compared word by word (difflib) when old words × new words is at most this
MMS_NEEDS = ("onnxruntime", "ctc_forced_aligner", "soundfile")


def sentences(book: dict) -> list[str]:
    """Every sentence's text in reading order, its whitespace evened out."""
    return [" ".join(b["text"][x:y].split()) for b in book["blocks"] for x, y in b.get("sentences") or []]


def _words(book: dict) -> tuple[list[str], list[int], list[int]]:
    """The book's normalized words, the sentence each is in, and for each sentence the index of its first
    word (for a sentence without words: of the next word in the book)."""
    norm: list[str] = []
    sent_of: list[int] = []
    start: list[int] = []
    for b in book["blocks"]:
        text = b.get("text", "")
        for x, y in b.get("sentences") or []:
            start.append(len(norm))
            for m in WORD_RE.finditer(text, x, y):
                n = normalize(m.group())
                if n:
                    norm.append(n)
                    sent_of.append(len(start) - 1)
    return norm, sent_of, start


def _unique_pairs(a: list[str], alo: int, ahi: int, b: list[str], blo: int, bhi: int) -> list[tuple[int, int]]:
    """Words found exactly once on each side, paired, keeping the longest run that is in order on both."""
    ca, cb = Counter(a[alo:ahi]), Counter(b[blo:bhi])
    where = {b[j]: j for j in range(blo, bhi) if cb[b[j]] == 1}
    pairs = [(i, where[a[i]]) for i in range(alo, ahi) if ca[a[i]] == 1 and a[i] in where]
    tails: list[int] = []  # patience sorting: the longest increasing run of new positions
    ends: list[int] = []
    prev: list[int | None] = [None] * len(pairs)
    for k, (_, j) in enumerate(pairs):
        p = bisect_left(tails, j)
        if p == len(tails):
            tails.append(j)
            ends.append(k)
        else:
            tails[p], ends[p] = j, k
        prev[k] = ends[p - 1] if p else None
    out = []
    k = ends[-1] if ends else None
    while k is not None:
        out.append(pairs[k])
        k = prev[k]
    return out[::-1]


def match_words(a: list[str], b: list[str]) -> list[int]:
    """For each word of `a`, the index of the same word in `b`, or -1; in order on both sides. Equal ends
    first, then words unique on both sides as fixed points (patience diff), difflib in short stretches."""
    out = [-1] * len(a)
    todo = [(0, len(a), 0, len(b))]
    while todo:
        alo, ahi, blo, bhi = todo.pop()
        while alo < ahi and blo < bhi and a[alo] == b[blo]:
            out[alo] = blo
            alo, blo = alo + 1, blo + 1
        while alo < ahi and blo < bhi and a[ahi - 1] == b[bhi - 1]:
            ahi, bhi = ahi - 1, bhi - 1
            out[ahi] = bhi
        if alo >= ahi or blo >= bhi:
            continue
        if (ahi - alo) * (bhi - blo) <= SMALL:
            for i, j, n in SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False).get_matching_blocks():
                for k in range(n):
                    out[alo + i + k] = blo + j + k
            continue
        pi, pj = alo, blo
        for i, j in _unique_pairs(a, alo, ahi, b, blo, bhi):
            out[i] = j
            todo.append((pi, i, pj, j))
            pi, pj = i + 1, j + 1
        if pi > alo:  # without a fixed point the stretch stays unmatched: its words are placed between neighbours
            todo.append((pi, ahi, pj, bhi))
    return out


def sentence_map(old: dict, new: dict) -> tuple[list[int], int, int]:
    """(the new sentence index for each old one, old sentences found by their first word, old sentences
    with words). A first word the new text lost is placed after the last word found before it, never past
    the next one found. Monotonic."""
    a, _, a_start = _words(old)
    b, b_sent, b_start = _words(new)
    if not b_start:
        return [0] * len(a_start), 0, 0
    hit = match_words(a, b)
    place = [0] * len(a)
    nxt: list[tuple[int, int] | None] = [None] * len(a)
    following = None
    for i in range(len(a) - 1, -1, -1):
        if hit[i] >= 0:
            following = (i, hit[i])
        nxt[i] = following
    last: tuple[int, int] | None = None
    for i in range(len(a)):
        if hit[i] >= 0:
            place[i] = hit[i]
            last = (i, hit[i])
            continue
        after = nxt[i]
        if last is None and after is None:
            guess = i * len(b) // max(1, len(a))
        elif last is None:
            guess = after[1] - (after[0] - i)
        else:
            guess = last[1] + (i - last[0])
            if after is not None:
                guess = min(guess, after[1])
        place[i] = min(max(guess, 0), max(0, len(b) - 1))
    out: list[int] = []
    found = with_words = 0
    for s, k in enumerate(a_start):
        has_words = k < (a_start[s + 1] if s + 1 < len(a_start) else len(a))
        with_words += has_words
        found += has_words and hit[k] >= 0
        ns = b_sent[place[k]] if k < len(a) and b else len(b_start) - 1
        out.append(max(ns, out[-1]) if out else ns)
    return out, found, with_words


def compose(existing: dict[str, list[int]], old_edition: str, m: list[int]) -> dict[str, list[int]]:
    """editions.json after this extraction: every older edition's map led on through `m`, and `m` itself."""
    if not m:
        return {}
    out = {ed: [m[min(max(x, 0), len(m) - 1)] for x in prev] for ed, prev in existing.items() if ed != old_edition}
    if old_edition:
        out[old_edition] = m
    return out


def read_editions(d: Path) -> tuple[str, dict[str, list[int]]]:
    """The edition the book's maps lead to and the maps, as state.py reads them: entries that are not lists of
    whole numbers left out; a file that names no edition is no map ("", {})."""
    try:
        data = json.loads((d / "editions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", {}
    if not isinstance(data, dict) or not isinstance(data.get("edition"), str) or not isinstance(data.get("maps"), dict):
        return "", {}
    ok = lambda v: isinstance(v, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in v)  # noqa: E731
    return data["edition"], {k: v for k, v in data["maps"].items() if ok(v)}


def timing_source(d: Path) -> str:
    try:
        with (d / "timing.json").open("rb") as fh:
            m = re.search(r'"source":\s*"(\w+)"', fh.read(200).decode("utf-8", "ignore"))
    except OSError:
        return ""
    return m.group(1) if m else ""


def retime_refusal(d: Path, mms: bool) -> str:
    """Why the timing of this book cannot be built again here, or ""."""
    if not [*d.glob("yt.*.json3"), *[f for f in (d / "whisper.json3",) if f.exists()]]:
        return "the narrated words moved and no captions are kept in the book (yt.*.json3): timing cannot be rebuilt"
    if not any((d / n).exists() for n in PLAYABLE):
        return "the narrated words moved and the book has no audio file: timing cannot be rebuilt"
    if mms:
        missing = [m for m in MMS_NEEDS if importlib.util.find_spec(m) is None]
        missing += [] if shutil.which("ffmpeg") else ["ffmpeg"]
        if missing:
            return f"the timing is MMS-aligned and {', '.join(missing)} is not available here: it cannot be rebuilt"
    return ""


def _update_toml(d: Path, values: dict[str, str]) -> None:
    """Text-derived lines of book.toml (an empty value drops its line); its time stays, as stamp keeps it."""
    toml = d / "book.toml"
    st = toml.stat()
    old = text = toml.read_text(encoding="utf-8")
    for k, v in values.items():
        text = _set(text, k, v) if v else re.sub(rf"(?m)^{k}\s*=.*\n?", "", text)
    if text == old:
        return
    tmp = d / ".book.toml.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, toml)


def all_words(book: dict) -> int:
    """Words of the text and of the notes: a note moved out of the text is not lost."""
    notes = (v if isinstance(v, str) else v.get("text", "") for v in book.get("notes", {}).values())
    return sum(len(b["text"].split()) for b in book["blocks"]) + sum(len(n.split()) for n in notes)


def pictures(book: dict) -> set[str]:
    """Every picture the book shows: block images, pictures in the text and in the notes."""
    out = {im["src"] if isinstance(im, dict) else im for b in book["blocks"] for im in b.get("images", [])}
    out |= {p["src"] for b in book["blocks"] for p in b.get("pics", [])}
    for v in book.get("notes", {}).values():
        if isinstance(v, dict):
            out |= {p["src"] for p in v.get("pics", [])}
    return out


# a new extraction with fewer words than this share of the old one has lost text: it is not landed
KEEP_WORDS = 0.97


def reextract(d: Path, sources: list[str] | None = None, dry_run: bool = False, allow_loss: bool = False) -> dict:
    """Extract the book in `d` again; SystemExit with the reason when it cannot be done."""
    if not (d / "book.json").is_file() or not (d / "book.toml").is_file():
        raise SystemExit(f"{d}: not a book of the library (book.json and book.toml are needed)")
    meta = tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))
    # a file named on the command line is kept by its full path: the next run may start anywhere
    given = [s if is_url(s) else str(Path(s).expanduser().absolute()) for s in sources or []]
    srcs = given or [s.strip() for s in str(meta.get("text_source") or "").split(" | ") if s.strip()]
    if not srcs:
        raise SystemExit(f"{d.name}: book.toml names no text_source; pass --source")
    gone = [s for s in srcs if not is_url(s) and not Path(s).expanduser().is_file()]
    if gone:
        raise SystemExit(f"{d.name}: the source is no longer there: {', '.join(gone)}; pass --source")
    w = take(work_dir(d.name))
    if w is None:
        raise SystemExit(f"{d.name}: a job is working on this book now; try again once it is done")
    try:
        return _reextract(d, meta, srcs, bool(sources), w, dry_run, allow_loss)
    finally:
        if not dry_run:
            tidy(d)
        shutil.rmtree(w, ignore_errors=True)


def _reextract(
    d: Path, meta: dict, srcs: list[str], new_source: bool, w: Path, dry_run: bool, allow_loss: bool = False
) -> dict:
    old = json.loads((d / "book.json").read_text(encoding="utf-8"))
    title = clean_title(meta.get("title") or old.get("title") or d.name)
    build_text(srcs, d, title, str(meta.get("author") or ""), w)
    new = json.loads((w / "book.json").read_text(encoding="utf-8"))
    old_sents, new_sents = sentences(old), sentences(new)
    same = old_sents == new_sents
    m, found, with_words = ([], 0, 0) if same else sentence_map(old, new)
    old_edition = str(meta.get("edition") or "")
    target, maps = read_editions(d)
    # an earlier run landed the map to `target` and the text, and was stopped before the stamp: finish it
    unstamped = same and bool(target) and target != old_edition and old_edition in maps
    has_timing = (d / "timing.json").exists()
    mms = timing_source(d) == "mms"
    retime = has_timing and book_words(old) != book_words(new)
    refusal = retime_refusal(d, mms) if retime else ""
    if refusal:
        timing = refusal
    elif not has_timing:
        timing = "no audio"
    elif not retime:
        timing = "unchanged, the narrated words are where they were"
    else:
        timing = "rebuilt from the captions" + (", then aligned by MMS" if mms else "")
    summary = {
        "blocks": (len(old["blocks"]), len(new["blocks"])),
        "sentences": (len(old_sents), len(new_sents)),
        "edition": "new" if not same else "unstamped" if unstamped else "kept",
        "found": found,
        "with_words": with_words,
        "timing": timing,
        "words": (all_words(old), all_words(new)),
        "pictures_lost": sorted(pictures(old) - pictures(new)),
    }
    blocks = f"blocks {len(old['blocks'])} -> {len(new['blocks'])}"
    print(f"{d.name}: {blocks}, sentences {len(old_sents)} -> {len(new_sents)}")
    if unstamped:
        print("edition: the one an interrupted run landed and did not stamp, stamped now")
    else:
        print("edition: " + ("kept, every sentence reads as before" if same else "new"))
    if not same:
        pct = f" ({found / with_words:.1%})" if with_words else ""
        print(f"mapping: {found} of {with_words} old sentences found by their first word{pct}, the rest in between")
    print("timing: " + timing)
    ow, nw = summary["words"]
    lost = nw < ow * KEEP_WORDS
    print(f"words with notes: {ow} -> {nw}" + (" LOST TEXT" if lost else ""), flush=True)
    gone = summary["pictures_lost"]
    if gone:
        print(f"pictures no longer shown: {len(gone)} ({', '.join(gone[:5])})", flush=True)
    if (lost or gone) and not allow_loss:
        what = f"{ow - nw} words fewer" if lost else f"{len(gone)} pictures fewer"
        raise SystemExit(f"{d.name}: the new text has {what}; nothing changed (--allow-loss to land it)")
    if refusal:
        raise SystemExit(f"{d.name}: {refusal}; nothing changed")
    if dry_run:
        return summary

    if retime:  # as add_book times a new text under the captions it has
        run([PY, str(PIPE / "anchors.py"), str(d), "--work", str(w)])
        run([PY, str(PIPE / "timing_from_anchors.py"), str(w)])
    values = {"fragment_note": fragment_note(new)}
    if unstamped:
        values["edition"] = target
    if not same:
        values["edition"] = uuid.uuid4().hex  # chosen now: the map names the edition it leads to
        # maps leading elsewhere (an earlier run cut short before its text landed) do not lead to this text
        composed = compose(maps if target == old_edition else {}, old_edition, m)
        editions = {"edition": values["edition"], "maps": composed}
        (w / "editions.json").write_text(json.dumps(editions, separators=(",", ":")), encoding="utf-8")
        land(w / "editions.json", d / "editions.json")  # harmless before the stamp: it leads to no edition yet
    if retime:
        land(w / "timing.json", d / "timing.json")
    images = sorted((w / "images").iterdir()) if (w / "images").is_dir() else []
    if images:
        (d / "images").mkdir(exist_ok=True)
    for f in images:
        land(f, d / "images" / f.name)
    land(w / "book.json", d / "book.json")
    if new_source:
        values["text_source"] = " | ".join(srcs)
    edition = values.pop("edition", "")
    _update_toml(d, values)
    stamp(d, edition=edition)  # last, the edition with the sizes in one step: once it lands, its files are in place
    if retime and mms:
        print("running precise MMS alignment (about 15 min per hour of audio, low priority)...", flush=True)
        run([PY, str(PIPE / "align.py"), str(d), "--work", str(w)])
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--source", action="append", default=[], help="instead of text_source; repeat for parts")
    ap.add_argument("--dry-run", action="store_true", help="compare and report, write nothing into the book")
    ap.add_argument("--allow-loss", action="store_true", help="land a text with fewer words than before")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # a stop unwinds, so the work dir goes
    reextract(args.book_dir.expanduser(), args.source or None, args.dry_run, args.allow_loss)


if __name__ == "__main__":
    main()
