"""Transcription without captions: the WAV is read a stretch at a time and the times stay the book's; the
packages it needs are installed once where the Mac app keeps such packages. No network, no real recognizer."""

from __future__ import annotations

import importlib.machinery
import json
import subprocess
import sys
import types
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import needs  # noqa: E402
import transcribe  # noqa: E402


class Word:
    def __init__(self, word: str, start: float):
        self.word, self.start = word, start


class Seg:
    def __init__(self, start: float, end: float, words: list[Word]):
        self.start, self.end, self.words = start, end, words


class FakeModel:
    """One word one second into every stretch it is handed; remembers how long each stretch was."""

    lengths: list[float] = []

    def __init__(self, *a, **kw):
        FakeModel.lengths = []

    def transcribe(self, audio, **kw):
        FakeModel.lengths.append(len(audio) / transcribe.SR)
        assert audio.dtype == np.float32 and float(np.abs(audio).max()) <= 1.0
        return iter([Seg(1.0, 1.5, [Word(" слово", 1.0)])]), None


def fake_whisper(monkeypatch) -> None:
    mod = types.ModuleType("faster_whisper")
    mod.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", None)
    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(sys, "path", list(sys.path))  # need() adds the app's folder: not for the other tests


def write_wav(path: Path, seconds: float) -> None:
    n = int(seconds * transcribe.SR)
    samples = (np.sin(np.arange(n) * 0.05) * 20000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(transcribe.SR)
        w.writeframes(samples.tobytes())


def test_stretches_keep_the_recording_time(tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    monkeypatch.setattr(transcribe, "CHUNK_SEC", 4)
    write_wav(tmp_path / "audio16k.wav", 10)
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(tmp_path)])
    transcribe.main()
    assert FakeModel.lengths == [4, 4, 2]
    events = json.loads((tmp_path / "whisper.json3").read_text(encoding="utf-8"))["events"]
    assert [e["tStartMs"] for e in events] == [1000, 5000, 9000]
    assert events[0]["segs"] == [{"utf8": " слово", "tOffsetMs": 0}]


def test_rf64_is_read_whole(tmp_path, monkeypatch):
    """Past 4 GB the pipeline writes RF64 (a plain WAV header wraps its size there): it reads like any WAV."""
    import soundfile as sf

    fake_whisper(monkeypatch)
    monkeypatch.setattr(transcribe, "CHUNK_SEC", 4)
    sf.write(str(tmp_path / "audio16k.wav"), np.zeros(6 * transcribe.SR, dtype=np.int16), transcribe.SR, format="RF64")
    assert (tmp_path / "audio16k.wav").read_bytes()[:4] == b"RF64"
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(tmp_path)])
    transcribe.main()
    assert FakeModel.lengths == [4, 2]


def test_limit_reads_only_the_start(tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    write_wav(tmp_path / "audio16k.wav", 10)
    monkeypatch.setattr(sys, "argv", ["transcribe.py", str(tmp_path), "--limit-sec", "3"])
    transcribe.main()
    assert FakeModel.lengths == [3]


def test_need_installs_once_into_libs(tmp_path, monkeypatch):
    libs = tmp_path / "pylibs"
    monkeypatch.setattr(needs, "LIBS", libs)
    monkeypatch.setattr(sys, "path", list(sys.path))
    calls = []

    def pip(cmd, **kw):  # stands in for pip: what it installs is a module in --target
        calls.append(cmd)
        target = Path(cmd[cmd.index("--target") + 1])
        (target / "readsync_fake_pkg.py").write_text("X = 1\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(needs.subprocess, "run", pip)
    needs.need(("readsync_fake_pkg",), ["fake-pkg==1.0"], "проверку")
    needs.need(("readsync_fake_pkg",), ["fake-pkg==1.0"], "проверку")
    assert len(calls) == 1 and calls[0][-1] == "fake-pkg==1.0"
    assert str(libs) in sys.path
    sys.modules.pop("readsync_fake_pkg", None)


def test_need_says_so_when_pip_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(needs, "LIBS", tmp_path / "pylibs")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(needs.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    try:
        needs.need(("readsync_absent_pkg",), ["absent"], "проверку")
    except SystemExit as e:
        assert "не вышло поставить проверку" in str(e)
    else:
        raise AssertionError("no exit")
