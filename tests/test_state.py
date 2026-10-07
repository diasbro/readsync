"""Reading state per device: the merge contract shared with the iPhone app, and the files on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import state

VECTORS = json.loads((Path(__file__).parent / "state_vectors.json").read_text(encoding="utf-8"))


def test_shared_merge_vectors():
    for v in VECTORS:
        assert state.merge(v["files"], v["edition"], v.get("editions")) == v["merged"], v["name"]


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


def own(d: Path, device: str = "a" * 32) -> Path:
    return d / "state" / f"{device}.json"


def test_an_unreadable_own_file_is_never_written_over(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    own(d).parent.mkdir()
    own(d).write_text('{"pos": 42, "posAt": 1, "stats": {"da', encoding="utf-8")  # half-downloaded
    (d / "state" / ("b" * 32 + ".json")).write_text(json.dumps({"pos": 7, "posAt": 5}), encoding="utf-8")
    with pytest.raises(state.StateUnavailable):
        state.put(d, {"pos": 9, "posAt": 9})
    with pytest.raises(state.StateUnavailable):
        state.add_session(d, "2026-10-01", 10, 1)
    assert own(d).read_text(encoding="utf-8") == '{"pos": 42, "posAt": 1, "stats": {"da'
    assert state.load(d)["pos"] == 7  # what can be read is still merged


def test_an_icloud_placeholder_for_the_own_file_is_never_written_over(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    (d / "state").mkdir()
    (d / "state" / ("." + "a" * 32 + ".json.icloud")).write_bytes(b"bplist00")
    with pytest.raises(state.StateUnavailable):
        state.put(d, {"pos": 9, "posAt": 9})
    with pytest.raises(state.StateUnavailable):
        state.add_session(d, "2026-10-01", 10, 1)
    assert not own(d).exists()


def test_a_failed_save_leaves_the_cached_state_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    state.put(d, {"pos": 1, "posAt": 1})
    state.add_session(d, "2026-10-01", 10, 1)
    before = json.loads(json.dumps(state._last_good[own(d)]))
    own(d).write_text("{", encoding="utf-8")  # unreadable now: the change starts from the cached value

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(state, "_save_own", fail)
    with pytest.raises(OSError):
        state.put(d, {"pos": 2, "posAt": 2})
    with pytest.raises(OSError):
        state.add_session(d, "2026-10-01", 50, 5)
    assert state._last_good[own(d)] == before


def test_the_old_single_files_sentence_keeps_the_edition_it_was_read_in(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path, "e1")
    (d / "state.json").write_text(json.dumps({"sent": 300, "sentAt": 5, "sentPct": 20}), encoding="utf-8")
    assert state.load(d)["sent"] == 300
    assert json.loads(own(d).read_text(encoding="utf-8"))["sentEdition"] == "e1"
    assert not (d / "state.json").exists()
    (d / "book.toml").write_text('title = "B"\nedition = "e2"\n', encoding="utf-8")
    assert "sent" not in state.load(d)


def test_the_old_single_file_without_a_sentence_moves_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    (d / "state.json").write_text(json.dumps({"pos": 42, "posAt": 1}), encoding="utf-8")
    state.load(d)
    assert json.loads(own(d).read_text(encoding="utf-8")) == {"pos": 42, "posAt": 1}


def test_odd_values_are_skipped_not_fatal(tmp_path, monkeypatch):
    files = [
        {
            "pos": 1,
            "posAt": "yesterday",
            "stats": {"days": {"2026-10-01": "x", "2026-10-02": {"sec": "a", "words": 3}}},
        },
        {"pos": 2, "posAt": 1, "mode": "audio", "modeAt": None, "stats": "none"},
        {"stats": {"days": ["2026-10-03"]}},
    ]
    assert state.merge(files, "") == {
        "pos": 2,
        "posAt": 1,
        "mode": "audio",
        "modeAt": 0,
        "stats": {"days": {"2026-10-02": {"sec": 0.0, "words": 3.0}}},
    }
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    (d / "state").mkdir()
    (d / "state" / ("b" * 32 + ".json")).write_text("[1, 2]", encoding="utf-8")
    assert state.load(d) == {}
    state.put(d, {"pos": 5, "posAt": 3})
    assert state.put(d, {"pos": 6, "posAt": "now"})["pos"] == 5
    assert json.loads(own(d).read_text(encoding="utf-8"))["posAt"] == 3


def test_a_device_id_that_is_not_one_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    f = tmp_path / "Library" / "Application Support" / "readsync" / "device-id"
    monkeypatch.setenv("READSYNC_DEVICE", "My Mac")
    first = state.device_id()
    assert state.DEVICE_ID.match(first) and f.read_text(encoding="utf-8") == first
    assert state.device_id() == first
    f.write_text("../../escape\n", encoding="utf-8")
    second = state.device_id()
    assert state.DEVICE_ID.match(second) and f.read_text(encoding="utf-8") == second
    monkeypatch.setenv("READSYNC_DEVICE", "c" * 32)
    assert state.device_id() == "c" * 32
    assert sorted(p.name for p in f.parent.iterdir()) == ["device-id"]


STATUS_VECTORS = json.loads((Path(__file__).parent / "status_vectors.json").read_text(encoding="utf-8"))


def test_shared_status_vectors():
    for v in STATUS_VECTORS:
        assert state.status(v["state"], v["audio"], v["atEnd"]) == v["expect"], v["name"]


def test_a_done_patch_writes_both_keys_and_an_older_one_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path)
    state.put(d, {"shelf": "done", "shelfAt": 50, "finished": ["2026-10-07"], "finishedAt": 50})
    on_disk = json.loads(own(d).read_text(encoding="utf-8"))
    assert on_disk["shelf"] == "done" and on_disk["finished"] == ["2026-10-07"] and on_disk["finishedAt"] == 50
    merged = state.put(d, {"shelf": "reading", "shelfAt": 40, "finished": [], "finishedAt": 40})
    assert merged["shelf"] == "done" and merged["finished"] == ["2026-10-07"]


def test_a_sentence_from_a_page_of_the_old_text_is_not_taken(tmp_path, monkeypatch):
    """A tab left open across a text replacement still counts sentences in the old text: its page turns
    must not land as positions in the new one. A page that names no edition is taken as before."""
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path, "e2")
    state.put(d, {"sent": 10, "sentAt": 1, "sentEdition": "e2"})

    merged = state.put(d, {"sent": 1234, "sentAt": 2, "sentEdition": "e1"})

    assert merged["sent"] == 10
    assert state.put(d, {"sent": 11, "sentAt": 3})["sent"] == 11


def test_a_text_extracted_again_keeps_the_page(tmp_path, monkeypatch):
    """editions.json maps the old edition's sentences: the position read in it is translated, and a tab
    still showing the old text saves its page in the new numbering."""
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path, "e1")
    state.put(d, {"sent": 3, "sentAt": 5, "sentPct": 20, "sentEdition": "e1"})
    maps = {"e1": [0, 1, 1, 2, 4], "e0": "broken"}
    (d / "editions.json").write_text(json.dumps({"edition": "e2", "maps": maps}), encoding="utf-8")
    assert state.load(d)["sent"] == 3  # the map leads to the next edition: not used while the book is e1
    (d / "book.toml").write_text('title = "B"\nedition = "e2"\n', encoding="utf-8")

    assert state.load(d)["sent"] == 2
    assert state.editions_of(d) == {"edition": "e2", "maps": {"e1": [0, 1, 1, 2, 4]}}

    merged = state.put(d, {"sent": 4, "sentAt": 6, "sentPct": 30, "sentEdition": "e1"})
    assert merged["sent"] == 4
    saved = json.loads(own(d).read_text(encoding="utf-8"))
    assert (saved["sent"], saved["sentEdition"]) == (4, "e2")
    assert state.put(d, {"sent": 9, "sentAt": 7, "sentEdition": "e0"})["sent"] == 4  # no usable map for e0


def test_a_map_leading_to_another_edition_is_not_used(tmp_path, monkeypatch):
    """editions.json names the edition its maps lead to: a text replaced since (or a new edition stamped by
    hand) is not that edition, so a stale map is ignored instead of taking its indices for the new text's."""
    monkeypatch.setenv("READSYNC_DEVICE", "c" * 32)
    d = book(tmp_path, "e3")
    (d / "state").mkdir()
    (d / "editions.json").write_text(json.dumps({"edition": "e2", "maps": {"e1": [0, 5, 9, 12]}}), encoding="utf-8")
    (d / "state" / ("b" * 32 + ".json")).write_text(
        json.dumps({"sent": 2, "sentAt": 5, "sentEdition": "e1"}), encoding="utf-8"
    )
    assert "sent" not in state.load(d)
    assert "sent" not in state.put(d, {"sent": 1, "sentAt": 9, "sentEdition": "e1"})
    (d / "editions.json").write_text(json.dumps({"e1": [0, 5, 9, 12]}), encoding="utf-8")  # no edition named: no map
    assert state.editions_of(d) == {} and "sent" not in state.load(d)


def test_the_merged_sentence_names_its_edition(tmp_path, monkeypatch):
    """A page that loaded another text can tell the merged sentence is not in its numbering."""
    monkeypatch.setenv("READSYNC_DEVICE", "a" * 32)
    d = book(tmp_path, "e2")
    (d / "editions.json").write_text(json.dumps({"edition": "e2", "maps": {"e1": [0, 3, 4]}}), encoding="utf-8")
    assert "sentEdition" not in state.put(d, {"pos": 1, "posAt": 1})
    merged = state.put(d, {"sent": 1, "sentAt": 2, "sentEdition": "e1"})
    assert (merged["sent"], merged["sentEdition"]) == (3, "e2")
    assert state.load(d)["sentEdition"] == "e2"
