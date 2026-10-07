"""Extracting a library book again: the edition kept when every sentence reads the same, else a map of
the old sentences into the new ones (editions.json), and the timing of an audiobook rebuilt from its
captions. Scratch books only; the extractors run for real on a plain-text source, no network."""

from __future__ import annotations

import json
import random
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import reextract  # noqa: E402
from anchors import book_words  # noqa: E402
from extract_txt import build, extract  # noqa: E402
from tidy import claim, work_dir  # noqa: E402

import state  # noqa: E402

SYLLABLES = [
    "ка",
    "ро",
    "ми",
    "ла",
    "ту",
    "не",
    "сво",
    "да",
    "пе",
    "ри",
    "го",
    "ше",
    "бу",
    "ле",
    "зо",
    "ва",
    "ны",
    "ти",
    "мо",
    "жа",
]


@pytest.fixture(autouse=True)
def work_root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("READSYNC_WORK", str(tmp_path / "work"))
    return tmp_path / "work"


def paragraphs(n: int, seed: int = 1) -> list[str]:
    """`n` paragraphs of made-up words, three or four sentences each; every word differs from its neighbours."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        sents = []
        for _ in range(rnd.randint(3, 4)):
            words = ["".join(rnd.choice(SYLLABLES) for _ in range(rnd.randint(2, 4))) for _ in range(rnd.randint(4, 9))]
            sents.append(" ".join(words).capitalize() + ".")
        out.append(" ".join(sents))
    return out


def as_book(paras: list[str]) -> dict:
    return build(paras, "Книга")


def sent_texts(book: dict) -> list[str]:
    return reextract.sentences(book)


def write_source(path: Path, paras: list[str]) -> Path:
    path.write_text("\n\n".join(paras) + "\n", encoding="utf-8")
    return path


def library_book(tmp_path: Path, paras: list[str]) -> tuple[Path, Path]:
    """A book as add_book left it: its text from a .txt source, stamped, with a reading state."""
    src = write_source(tmp_path / "source.txt", paras)
    d = tmp_path / "books" / "kniga"
    d.mkdir(parents=True)
    (d / "book.json").write_text(json.dumps(extract(src), ensure_ascii=False), encoding="utf-8")
    (d / "book.toml").write_text(
        f'slug = "kniga"\ntitle = "Книга"\ntext_source = "{src}"\nid = "{"a" * 32}"\nedition = "e1"\n',
        encoding="utf-8",
    )
    (d / "state").mkdir()
    (d / "state" / ("b" * 32 + ".json")).write_text(
        json.dumps({"sent": 20, "sentAt": 5, "sentPct": 10, "sentEdition": "e1"}), encoding="utf-8"
    )
    return d, src


def toml(d: Path) -> dict:
    return tomllib.loads((d / "book.toml").read_text(encoding="utf-8"))


def snapshot(d: Path) -> dict:
    return {p.relative_to(d): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(d.rglob("*")) if p.is_file()}


# ---- the map ----


def test_the_same_text_maps_onto_itself():
    book = as_book(paragraphs(30))
    m, found, with_words = reextract.sentence_map(book, book)
    assert m == list(range(len(sent_texts(book))))
    assert found == with_words == len(m)


def test_sentences_joined_and_split_map_to_the_sentence_holding_their_first_word():
    old = {"blocks": [{"text": "Повод Н. Жирардо пришёл. Ушёл.", "sentences": [[0, 8], [9, 24], [25, 30]]}]}
    new = {"blocks": [{"text": "Повод Н. Жирардо пришёл. Ушёл.", "sentences": [[0, 24], [25, 30]]}]}
    assert reextract.sentence_map(old, new)[0] == [0, 0, 1]
    assert reextract.sentence_map(new, old)[0] == [0, 2]


def test_text_taken_out_and_put_in_keeps_the_rest_in_place():
    """A footnote body read as text goes, a lost paragraph comes back: every other sentence finds itself."""
    paras = paragraphs(40)
    old_paras = [*paras[:10], "* Сноска о чём-то далёком.", *paras[10:]]
    new_paras = [*paras[:25], "Вернувшийся абзац. Ещё одна фраза.", *paras[25:]]
    old, new = as_book(old_paras), as_book(new_paras)
    m, found, _ = reextract.sentence_map(old, new)
    olds, news = sent_texts(old), sent_texts(new)
    assert m == sorted(m) and len(m) == len(olds)
    for i, text in enumerate(olds):
        if text != "* Сноска о чём-то далёком.":
            assert news[m[i]] == text, i
    footnote = olds.index("* Сноска о чём-то далёком.")
    assert m[footnote] == m[footnote + 1]  # its place is the sentence after it
    assert found == len(olds) - 1


def test_a_long_book_with_scattered_changes_maps_without_comparing_every_word_to_every_word(monkeypatch):
    monkeypatch.setattr(reextract, "SMALL", 400)  # force the unique-word fixed points on a book this size
    paras = paragraphs(300, seed=7)
    changed = list(paras)
    for i in range(5, 300, 37):
        changed[i] = changed[i].replace(".", ";", 1)  # two sentences become one
    old, new = as_book(paras), as_book(changed)
    m, found, with_words = reextract.sentence_map(old, new)
    news = sent_texts(new)
    for i, text in enumerate(sent_texts(old)):
        assert text.split()[0].lower() in news[m[i]].lower().split(), i  # the new sentence holds its first word
    assert found == with_words


def test_maps_compose_through_every_later_extraction():
    assert reextract.compose({"e0": [0, 2, 9]}, "e1", [0, 0, 1, 1, 3]) == {"e0": [0, 1, 3], "e1": [0, 0, 1, 1, 3]}
    assert reextract.compose({}, "e1", []) == {}


# ---- a book in the library ----


def test_a_changed_text_gets_a_new_edition_and_keeps_the_page(tmp_path, monkeypatch):
    paras = paragraphs(40)
    d, src = library_book(tmp_path, paras)
    state_before = snapshot(d / "state")
    old_sents = sent_texts(json.loads((d / "book.json").read_text(encoding="utf-8")))
    write_source(src, ["Новое предисловие. Его не было.", *paras])

    summary = reextract.reextract(d)

    meta = toml(d)
    assert summary["edition"] == "new" and meta["edition"] != "e1" and meta["id"] == "a" * 32
    editions = json.loads((d / "editions.json").read_text(encoding="utf-8"))
    assert editions["edition"] == meta["edition"]  # the edition the maps lead to
    maps = editions["maps"]
    assert list(maps) == ["e1"] and len(maps["e1"]) == len(old_sents)
    new_sents = sent_texts(json.loads((d / "book.json").read_text(encoding="utf-8")))
    assert new_sents[maps["e1"][20]] == old_sents[20]
    assert f"editions.json:{(d / 'editions.json').stat().st_size}" in meta["files"]
    assert isinstance(meta["text_end"], int)
    assert snapshot(d / "state") == state_before
    monkeypatch.setenv("READSYNC_DEVICE", "c" * 32)
    assert state.load(d)["sent"] == maps["e1"][20]  # the page read in the old text is where it was
    assert not (tmp_path / "work" / "kniga").exists()


def test_the_same_text_keeps_its_edition_and_its_map(tmp_path):
    paras = paragraphs(40)
    d, src = library_book(tmp_path, paras)
    write_source(src, ["Новое предисловие. Его не было.", *paras])
    reextract.reextract(d)
    edition, maps = toml(d)["edition"], (d / "editions.json").read_bytes()

    summary = reextract.reextract(d)

    assert summary["edition"] == "kept" and toml(d)["edition"] == edition
    assert (d / "editions.json").read_bytes() == maps


def test_another_source_composes_the_map(tmp_path):
    paras = paragraphs(40)
    d, src = library_book(tmp_path, paras)
    write_source(src, ["Новое предисловие. Его не было.", *paras])
    reextract.reextract(d)
    e2 = toml(d)["edition"]
    other = write_source(tmp_path / "other.txt", ["Новое предисловие. Его не было.", "И ещё одно.", *paras])

    reextract.reextract(d, [str(other)])

    meta = toml(d)
    editions = json.loads((d / "editions.json").read_text(encoding="utf-8"))
    maps = editions["maps"]
    assert sorted(maps) == sorted(["e1", e2]) and meta["text_source"] == str(other)
    assert editions["edition"] == meta["edition"] != e2
    new_sents = sent_texts(json.loads((d / "book.json").read_text(encoding="utf-8")))
    assert maps["e1"][0] == 3 and new_sents[3] == sent_texts(as_book(paras))[0]


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    paras = paragraphs(40)
    d, src = library_book(tmp_path, paras)
    write_source(src, paras[1:])
    before = snapshot(d)

    summary = reextract.reextract(d, dry_run=True)

    assert summary["edition"] == "new" and snapshot(d) == before
    out = capsys.readouterr().out
    assert "edition: new" in out and "mapping:" in out and "timing: no audio" in out


def test_a_text_that_lost_words_is_not_landed(tmp_path):
    paras = paragraphs(80)
    d, src = library_book(tmp_path, paras)
    write_source(src, paras[:60])
    before = snapshot(d)

    with pytest.raises(SystemExit, match="words fewer"):
        reextract.reextract(d)

    assert snapshot(d) == before
    summary = reextract.reextract(d, allow_loss=True)
    assert summary["words"][1] < summary["words"][0] and toml(d)["edition"] != "e1"


def test_a_source_that_is_gone_or_a_running_job_refuses(tmp_path):
    d, src = library_book(tmp_path, paragraphs(40))
    claim(work_dir("kniga"))  # this process holds the book's work dir, as a running job would
    with pytest.raises(SystemExit, match="a job is working"):
        reextract.reextract(d)
    src.unlink()
    with pytest.raises(SystemExit, match="no longer there"):
        reextract.reextract(d)


def test_a_run_stopped_before_the_stamp_is_finished_by_the_next(tmp_path, monkeypatch):
    """The map and the new text landed, book.toml did not: the map names the edition it leads to, so it is
    not taken for the old one meanwhile, and the next run stamps that edition instead of keeping the old."""
    monkeypatch.setenv("READSYNC_DEVICE", "c" * 32)
    paras = paragraphs(40)
    d, src = library_book(tmp_path, paras)
    old_sents = sent_texts(json.loads((d / "book.json").read_text(encoding="utf-8")))
    write_source(src, ["Новое предисловие. Его не было. И ещё.", *paras])

    def stopped(*_):
        raise KeyboardInterrupt("stopped before the stamp")

    with monkeypatch.context() as m:
        m.setattr(reextract, "_update_toml", stopped)
        with pytest.raises(KeyboardInterrupt):
            reextract.reextract(d)
    editions = json.loads((d / "editions.json").read_text(encoding="utf-8"))
    assert toml(d)["edition"] == "e1" and editions["edition"] != "e1" and "e1" in editions["maps"]
    assert state.load(d)["sent"] == 20  # the map leads to an edition the book does not have yet

    summary = reextract.reextract(d)

    assert summary["edition"] == "unstamped" and toml(d)["edition"] == editions["edition"]
    new_sents = sent_texts(json.loads((d / "book.json").read_text(encoding="utf-8")))
    assert new_sents[state.load(d)["sent"]] == old_sents[20]
    assert reextract.reextract(d)["edition"] == "kept"
    meta = (d / "book.toml").read_text(encoding="utf-8")
    (d / "book.toml").write_text(meta.replace(editions["edition"], "e9"), encoding="utf-8")  # stamped anew since
    assert reextract.reextract(d)["edition"] == "kept" and toml(d)["edition"] == "e9"  # a stale map is no stop


def test_a_relative_source_is_kept_by_its_full_path(tmp_path, monkeypatch):
    paras = paragraphs(40)
    d, _ = library_book(tmp_path, paras)
    other = write_source(tmp_path / "other.txt", ["Новое предисловие. Его не было.", *paras])
    monkeypatch.chdir(tmp_path)
    reextract.reextract(d, ["other.txt"])
    assert toml(d)["text_source"] == str(other)
    monkeypatch.chdir(d)
    assert reextract.reextract(d)["edition"] == "kept"  # found again from anywhere


def test_a_job_claiming_at_the_same_moment_keeps_its_work_dir(tmp_path):
    """The check and the claim are one step: a job claiming the work dir while reextract starts is either
    seen holding it or claims after; its work dir is never swept away under it."""
    import threading
    import time

    import tidy

    d, _ = library_book(tmp_path, paragraphs(40))
    w = work_dir("kniga")
    inside = threading.Event()

    def job():
        with tidy._claiming(w.parent):
            inside.set()
            time.sleep(0.3)
            tidy._claim(w)
            (w / "download").write_text("x", encoding="utf-8")

    t = threading.Thread(target=job)
    t.start()
    inside.wait()
    with pytest.raises(SystemExit, match="a job is working"):
        reextract.reextract(d)
    t.join()
    assert (w / "download").exists()


# ---- an audiobook ----


def captions_for(book: dict) -> tuple[dict, dict]:
    """json3 captions saying every narrated word at 0.4 s a word, and the caption timing add_book made."""
    words, _ = book_words(book)
    events = []
    for i, (b, x, y) in enumerate(words):
        events.append({"tStartMs": i * 400, "segs": [{"utf8": book["blocks"][b]["text"][x:y]}]})
    timing = {
        "source": "captions",
        "duration": len(words) * 0.4,
        "words": [[b, x, y, i * 0.4, i * 0.4 + 0.35] for i, (b, x, y) in enumerate(words)],
    }
    return {"events": events}, timing


def audio_book(tmp_path: Path, paras: list[str], source: str = "captions") -> tuple[Path, Path]:
    d, src = library_book(tmp_path, paras)
    caps, timing = captions_for(json.loads((d / "book.json").read_text(encoding="utf-8")))
    timing["source"] = source
    (d / "yt.merged.json3").write_text(json.dumps(caps, ensure_ascii=False), encoding="utf-8")
    (d / "timing.json").write_text(json.dumps(timing), encoding="utf-8")
    (d / "audio.m4a").write_bytes(b"not really audio")  # ffprobe finds no length: the captions give it
    return d, src


def test_an_audiobook_is_timed_again_from_its_captions(tmp_path):
    paras = paragraphs(40)
    d, src = audio_book(tmp_path, paras)
    write_source(src, [*paras[:5], "Это вставка без звука. Её не читали.", *paras[5:]])

    summary = reextract.reextract(d)

    assert summary["timing"] == "rebuilt from the captions"
    new = json.loads((d / "book.json").read_text(encoding="utf-8"))
    timing = json.loads((d / "timing.json").read_text(encoding="utf-8"))
    assert timing["source"] == "captions"
    assert [w[:3] for w in timing["words"]] == book_words(new)[0]
    starts = [w[3] for w in timing["words"]]
    assert starts == sorted(starts)
    old_words = book_words(as_book(paras))[0]
    first_after = next(w for w in timing["words"] if w[0] == 7)  # paragraph 6 of the old text moved one block on
    assert first_after[3] == pytest.approx(next(i for i, w in enumerate(old_words) if w[0] == 6) * 0.4)
    assert f"timing.json:{(d / 'timing.json').stat().st_size}" in toml(d)["files"]
    assert (d / "yt.merged.json3").exists() and (d / "audio.m4a").exists()


def test_an_audiobook_whose_words_stay_keeps_its_timing(tmp_path):
    paras = paragraphs(40)
    d, src = audio_book(tmp_path, paras)
    timing = (d / "timing.json").read_bytes()
    reextract.reextract(d)
    assert (d / "timing.json").read_bytes() == timing


def test_an_audiobook_without_captions_is_refused_and_left_as_it_was(tmp_path):
    paras = paragraphs(40)
    d, src = audio_book(tmp_path, paras)
    (d / "yt.merged.json3").unlink()
    write_source(src, paras[1:])
    before = snapshot(d)
    with pytest.raises(SystemExit, match="no captions"):
        reextract.reextract(d)
    assert snapshot(d) == before


def test_an_mms_timed_audiobook_without_the_aligner_is_refused(tmp_path, monkeypatch):
    paras = paragraphs(40)
    d, src = audio_book(tmp_path, paras, source="mms")
    write_source(src, paras[1:])
    real = reextract.importlib.util.find_spec
    monkeypatch.setattr(reextract.importlib.util, "find_spec", lambda n: None if n == "onnxruntime" else real(n))
    before = snapshot(d)
    with pytest.raises(SystemExit, match="onnxruntime"):
        reextract.reextract(d)
    assert snapshot(d) == before


def test_pictures_counts_block_images_text_pictures_and_note_pictures():
    book = {
        "blocks": [{"text": "a", "images": [{"src": "images/a.png"}], "pics": [{"pos": 0, "src": "images/b.png"}]}],
        "notes": {"n1": "plain", "n2": {"text": "x", "pics": [{"pos": 0, "src": "images/c.png"}]}},
    }
    assert reextract.pictures(book) == {"images/a.png", "images/b.png", "images/c.png"}
