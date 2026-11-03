"""Fallback for audio without YouTube captions: transcribe with faster-whisper into a json3-like
caption file (whisper.json3) that anchors.py understands.

The WAV is read here, a stretch at a time, rather than handed to faster-whisper by name: it would decode
the whole file into memory (a 100-hour book is 23 GB of samples), and its decoder breaks with each PyAV
release that renames an option. soundfile reads it, as align.py does: past 4 GB the WAV is RF64, which the
stdlib wave module does not know."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from needs import need  # noqa: E402

SR = 16000
CHUNK_SEC = 1800  # half an hour of samples at a time: about 115 MB as float32


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir", type=Path)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--limit-sec", type=float, default=None)
    args = ap.parse_args()
    need(("numpy", "soundfile", "faster_whisper"), ["faster-whisper==1.2.1", "soundfile"], "распознавание речи")
    import soundfile as sf
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    t = time.time()
    events = []
    n = 0
    with sf.SoundFile(str(args.book_dir / "audio16k.wav")) as wav:
        if (wav.samplerate, wav.channels) != (SR, 1):
            sys.exit("audio16k.wav must be 16 kHz mono")
        left = wav.frames if not args.limit_sec else min(wav.frames, int(args.limit_sec * SR))
        start = 0.0
        while left > 0:
            audio = wav.read(min(left, CHUNK_SEC * SR), dtype="float32")
            if not len(audio):
                break
            left -= len(audio)
            segments, _ = model.transcribe(
                audio, language=args.lang, word_timestamps=True, vad_filter=True, beam_size=1
            )
            for seg in segments:
                segs = [{"utf8": w.word, "tOffsetMs": int((w.start - seg.start) * 1000)} for w in (seg.words or [])]
                if segs:
                    events.append({"tStartMs": int((start + seg.start) * 1000), "segs": segs})
                    n += len(segs)
                if n and n % 2000 < len(segs):
                    done = (start + seg.end) / 3600
                    print(f"  {done:.2f}h transcribed, {(time.time() - t) / 60:.1f} min", flush=True)
            start += len(audio) / SR
    (args.book_dir / "whisper.json3").write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")
    print(f"words={n} events={len(events)} {(time.time() - t) / 60:.1f} min")


if __name__ == "__main__":
    main()
