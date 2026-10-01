"""Reading state per device: the merge contract shared with the iPhone app, and the files on disk."""

from __future__ import annotations

import json
from pathlib import Path

import state

VECTORS = json.loads((Path(__file__).parent / "state_vectors.json").read_text(encoding="utf-8"))


def test_shared_merge_vectors():
    for v in VECTORS:
        assert state.merge(v["files"], v["edition"]) == v["merged"], v["name"]


def book(tmp_path: Path, edition: str = "e1") -> Path:
    d = tmp_path / "b"
    d.mkdir()
    (d / "book.toml").write_text(f'title = "B"\nedition = "{edition}"\n', encoding="utf-8")
    return d


def test_the_old_single_file_becomes_this_macs(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    (d / "state.json").write_text(json.dumps({"pos": 42, "posAt": 1}), encoding="utf-8")
    assert state.load(d)["pos"] == 42
    assert not (d / "state.json").exists() and (d / "state" / ("a" * 32 + ".json")).exists()


def test_a_reader_echoing_merged_totals_does_not_double_them(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    (d / "state").mkdir()
    phone = {"stats": {"days": {"2026-10-01": {"sec": 100, "words": 10}}}}
    (d / "state" / ("b" * 32 + ".json")).write_text(json.dumps(phone), encoding="utf-8")
    state.add_session(d, "2026-10-01", 50, 5)
    merged = state.load(d)
    state.put(d, {"stats": merged["stats"], "pos": 7, "posAt": 2})
    assert state.load(d)["stats"]["days"]["2026-10-01"] == {"sec": 150.0, "words": 15.0}


def test_each_device_writes_only_its_own_file(tmp_path, monkeypatch):
    d = book(tmp_path)
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    state.put(d, {"pos": 1, "posAt": 1})
    monkeypatch.setenv("READSYNC_DEVICE", "b" * 32)
    state.put(d, {"pos": 2, "posAt": 2})
    assert sorted(p.name for p in (d / "state").iterdir()) == ["a" * 32 + ".json", "b" * 32 + ".json"]
    assert state.load(d)["pos"] == 2


def test_a_conflict_copy_is_not_another_device(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    state.add_session(d, "2026-10-01", 10, 1)
    copy = d / "state" / ("a" * 32 + " 2.json")
    copy.write_text((d / "state" / ("a" * 32 + ".json")).read_text(encoding="utf-8"), encoding="utf-8")
    assert state.load(d)["stats"]["days"]["2026-10-01"]["sec"] == 10.0


def test_a_new_edition_drops_the_old_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path, "e1")
    state.put(d, {"sent": 300, "sentAt": 5, "sentPct": 20})
    (d / "book.toml").write_text('title = "B"\nedition = "e2"\n', encoding="utf-8")
    assert "sent" not in state.load(d)
