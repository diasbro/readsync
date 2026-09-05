"""Build timing.json from anchors.json by interpolating unanchored words.

timing.json: {"source": "captions", "duration": float,
              "words": [[block_idx, char_start, char_end, t0, t1], ...]}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_WORD_SEC = 1.5


def interpolate(words: list[list[int]], anchors: list[list[float]], duration: float) -> list[list[float]]:
    n = len(words)
    t0 = [None] * n
    for wi, ts, _ in anchors:
        t0[wi] = ts
    # character weights: word length + 1 (space) as proxy for speaking time
    weight = [w[2] - w[1] + 1 for w in words]
    known = [i for i in range(n) if t0[i] is not None]
    if not known:
        raise SystemExit("no anchors")
    # global speaking rate (sec per char) from first/last anchors
    span_chars = sum(weight[known[0] : known[-1]]) or 1
    rate = (t0[known[-1]] - t0[known[0]]) / span_chars
    # before first anchor
    acc = 0
    for i in range(known[0] - 1, -1, -1):
        acc += weight[i]
        t0[i] = max(0.0, t0[known[0]] - acc * rate)
    # between anchors
    for a, b in zip(known, known[1:], strict=False):
        if b - a <= 1:
            continue
        seg_chars = sum(weight[a:b]) or 1
        seg_rate = (t0[b] - t0[a]) / seg_chars
        acc = 0
        for i in range(a, b - 1):
            acc += weight[i]
            t0[i + 1] = t0[a] + acc * seg_rate
    # after last anchor
    acc = 0
    for i in range(known[-1] + 1, n):
        acc += weight[i - 1]
        t0[i] = min(duration, t0[known[-1]] + acc * rate)
    out = []
    for i in range(n):
        nxt = t0[i + 1] if i + 1 < n else duration
        t1 = min(nxt, t0[i] + MAX_WORD_SEC)
        if t1 <= t0[i]:
            t1 = t0[i] + 0.05
        out.append([words[i][0], words[i][1], words[i][2], round(t0[i], 3), round(t1, 3)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    args = ap.parse_args()
    anc = json.loads((args.book_dir / "anchors.json").read_text(encoding="utf-8"))
    words = interpolate(anc["words"], anc["anchors"], anc["duration"])
    (args.book_dir / "timing.json").write_text(
        json.dumps({"source": "captions", "duration": anc["duration"], "words": words}), encoding="utf-8"
    )
    print(f"timing words={len(words)} duration={anc['duration']:.0f}s")


if __name__ == "__main__":
    main()
