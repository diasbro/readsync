"""tests/import_vectors is what the pipeline makes of its made-up books today. The phone's importer is tested
against those files, so a change to an extractor shows here until the vectors are made again."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_import_vectors_are_fresh():
    spec = importlib.util.spec_from_file_location("make_import_vectors", HERE / "make_import_vectors.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    have = {f.name: f.read_bytes() for f in m.OUT.iterdir()}
    stale = sorted(n for n, data in m.build().items() if have.pop(n, None) != data) + sorted(have)
    assert not stale, f"run tests/make_import_vectors.py: {stale}"
