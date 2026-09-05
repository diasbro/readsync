"""Fallback for audio without YouTube captions: transcribe with faster-whisper into a json3-like
caption file (whisper.json3) that anchors.py understands."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--limit-sec", type=float, default=None)
    args = ap.parse_args()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("pip install faster-whisper")
    wav = args.book_dir / "audio16k.wav"
    src = str(wav)
    if args.limit_sec:
        import soundfile as sf
        audio, sr = sf.read(str(wav), stop=int(args.limit_sec * 16000), dtype="float32")
        src = audio
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    t = time.time()
    segments, info = model.transcribe(src, language=args.lang, word_timestamps=True, vad_filter=True, beam_size=1)
    events = []
    n = 0
    for seg in segments:
        segs = [{"utf8": w.word, "tOffsetMs": int((w.start - seg.start) * 1000)} for w in (seg.words or [])]
        if segs:
            events.append({"tStartMs": int(seg.start * 1000), "segs": segs})
            n += len(segs)
        if n and n % 2000 < len(segs):
            print(f"  {seg.end / 3600:.2f}h transcribed, {(time.time() - t) / 60:.1f} min", flush=True)
    (args.book_dir / "whisper.json3").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    print(f"words={n} events={len(events)} {(time.time() - t) / 60:.1f} min")


if __name__ == "__main__":
    main()
