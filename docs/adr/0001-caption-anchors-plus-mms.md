# ADR 0001: caption anchors first, MMS forced alignment second

Date: 2026-09-05

## Context
An 11-hour audiobook cannot be force-aligned in one pass: CTC alignment memory grows with
audio frames × transcript length. Whisper transcription of the whole book on a laptop CPU takes
hours. YouTube auto-captions already carry per-word timestamps.

## Decision
1. Diff the book's words against caption words in sliding windows; runs of ≥3 equal words become
   anchors (94% coverage on the first book, median error 0.04 s vs. forced alignment).
2. Interpolate the rest by character length → a usable `timing.json` in under a second.
3. Optionally refine with the MMS aligner in ~2-minute windows bounded by anchors. Bad-scoring
   words keep the caption timing. Runs once per book, on efficiency cores unless `--fast`.

## Consequences
- Reading can start immediately; precision improves later without changing the reader.
- Audio without captions needs the faster-whisper fallback (`transcribe.py`) to produce anchors.
- The reader has a single time source (`timing.json`), so alignment strategy can change freely.
