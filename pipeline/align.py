"""Precise word alignment with the MMS CTC forced aligner (ONNX), windowed between caption anchors.

Reads  book.json, anchors.json, audio16k.wav
Writes timing.json  {"source": "mms", "duration": float, "words": [[block, cs, ce, t0, t1], ...]}

Strategy: cut the audio into windows of ~WINDOW_SEC bounded by confident anchor words, run the
aligner on each window with the book words that fall inside it, and take the resulting word
timestamps. Words the aligner scores badly (score < BAD_SCORE) fall back to caption/interpolated
timing so a single mis-read window cannot derail the map.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import stamp  # noqa: E402
from tidy import tidy  # noqa: E402
from timing_from_anchors import interpolate  # noqa: E402

SR = 16000
WINDOW_SEC = 120.0
PAD_SEC = 0.6
BAD_SCORE = -8.0
MAX_WORD_SEC = 1.5


def pick_windows(anchors: list[list[float]], duration: float) -> list[tuple[int, int, float, float]]:
    """Split anchor list into (word_lo, word_hi, t_lo, t_hi) windows of about WINDOW_SEC each.
    Window boundaries are anchor words (confident timestamps); word ranges are inclusive."""
    wins = []
    i = 0
    n = len(anchors)
    while i < n - 1:
        t_lo = anchors[i][1]
        j = i
        while j < n - 1 and anchors[j][1] - t_lo < WINDOW_SEC:
            j += 1
        # end window on anchor j: words [anchors[i].w, anchors[j].w - 1] and time [t_lo, anchors[j].t)
        w_lo, w_hi = int(anchors[i][0]), int(anchors[j][0]) - 1
        t_hi = anchors[j][1]
        if j == n - 1:
            w_hi = int(anchors[j][0])
            t_hi = min(duration, anchors[j][2] + 5.0)
        if w_hi >= w_lo:
            wins.append((w_lo, w_hi, t_lo, t_hi))
        i = j
    return wins


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--limit-sec", type=float, default=None, help="only align the first N seconds (for testing)")
    ap.add_argument("--providers", default="CPUExecutionProvider")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument(
        "--threads", type=int, default=4, help="CPU threads for the model (default 4 keeps the laptop cool)"
    )
    ap.add_argument("--fast", action="store_true", help="use all cores at normal priority")
    args = ap.parse_args()
    if not args.fast:
        os.nice(15)
        if sys.platform == "darwin":  # run on efficiency cores: cool and quiet, roughly 2x slower
            subprocess.run(["taskpolicy", "-b", "-p", str(os.getpid())], check=False)
    d = args.book_dir
    logging.getLogger("ctc_forced_aligner").setLevel(logging.ERROR)
    import ctc_forced_aligner as cfa

    book = json.loads((d / "book.json").read_text(encoding="utf-8"))
    if not (d / "anchors.json").exists():  # derived file, removed after every run: rebuilt from the captions
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "anchors.py"), str(d)], check=True)
    anc = json.loads((d / "anchors.json").read_text(encoding="utf-8"))
    words, anchors, duration = anc["words"], anc["anchors"], anc["duration"]
    base = interpolate(words, anchors, duration)  # fallback timing
    t0 = np.array([w[3] for w in base])
    t1 = np.array([w[4] for w in base])
    good = np.zeros(len(words), dtype=bool)

    al = cfa.AlignmentSingleton()
    model_path = al.model_path
    so = ort.SessionOptions()
    if not args.fast:
        so.intra_op_num_threads = args.threads
    session = ort.InferenceSession(model_path, sess_options=so, providers=args.providers.split(","))
    tokenizer = al.alignment_tokenizer
    wav_path = d / "audio16k.wav"
    if not wav_path.exists():  # derived file; rebuild it from whatever playable audio the book has
        src = next(f for f in (d / "audio.m4a", d / "audio.mp3", d / "yt.webm") if f.exists())
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            check=True,
        )
    wav = sf.SoundFile(str(wav_path))
    assert wav.samplerate == SR and wav.channels == 1, "audio16k.wav must be 16 kHz mono"

    wins = pick_windows(anchors, duration)
    if args.limit_sec:
        wins = [w for w in wins if w[2] < args.limit_sec]
    print(f"windows={len(wins)} words={len(words)} duration={duration:.0f}s", flush=True)
    started = time.time()
    bad_windows = 0
    for k, (w_lo, w_hi, tl, th) in enumerate(wins):
        a0 = max(0.0, tl - PAD_SEC)
        a1 = min(duration, th + PAD_SEC)
        wav.seek(int(a0 * SR))
        audio = wav.read(int((a1 - a0) * SR), dtype="float32")
        text = " ".join(book["blocks"][b]["text"][s:e] for b, s, e in words[w_lo : w_hi + 1])
        try:
            em, stride = cfa.generate_emissions(session, audio, batch_size=args.batch_size)
            tok, txt = cfa.preprocess_text(text, romanize=True, language="rus")
            seg, scores, blank = cfa.get_alignments(em, tok, tokenizer)
            spans = cfa.get_spans(tok, seg, blank)
            res = cfa.postprocess_results(txt, spans, stride, scores)
        except Exception as e:  # noqa: BLE001
            print(f"  window {k} [{tl:.0f}-{th:.0f}s] failed: {e}", flush=True)
            bad_windows += 1
            continue
        if len(res) != w_hi - w_lo + 1:
            print(f"  window {k}: token count mismatch {len(res)} vs {w_hi - w_lo + 1}", flush=True)
            bad_windows += 1
            continue
        for i, r in enumerate(res):
            wi = w_lo + i
            if r["score"] < BAD_SCORE:
                continue
            s, e = a0 + r["start"], a0 + r["end"]
            if e <= s:
                e = s + 0.05
            t0[wi], t1[wi] = s, e
            good[wi] = True
        if k % 20 == 0 or k == len(wins) - 1:
            el = time.time() - started
            print(
                f"  {k + 1}/{len(wins)} windows, {th / 3600:.2f}h audio, {el / 60:.1f} min elapsed, "
                f"eta {el / (k + 1) * (len(wins) - k - 1) / 60:.1f} min",
                flush=True,
            )

    # enforce monotonic order and sane durations
    for i in range(1, len(words)):
        if t0[i] < t0[i - 1]:
            t0[i] = t0[i - 1] + 0.01
    for i in range(len(words)):
        nxt = t0[i + 1] if i + 1 < len(words) else duration
        t1[i] = min(max(t1[i], t0[i] + 0.05), nxt, t0[i] + MAX_WORD_SEC)
    out = [[w[0], w[1], w[2], round(float(t0[i]), 3), round(float(t1[i]), 3)] for i, w in enumerate(words)]
    (d / "timing.json").write_text(json.dumps({"source": "mms", "duration": duration, "words": out}), encoding="utf-8")
    tidy(d)  # the 16 kHz copy and the anchors were only for this pass
    stamp(d)  # same edition, new timing.json size
    print(
        f"done: aligned {good.mean():.1%} of words by MMS, bad windows={bad_windows}, "
        f"{(time.time() - started) / 60:.1f} min",
        flush=True,
    )
    os._exit(0)  # onnxruntime CoreML teardown can crash at interpreter exit


if __name__ == "__main__":
    main()
