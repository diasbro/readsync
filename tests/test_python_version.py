"""The phone's importer follows the html.parser of the Python that builds the books on the Mac: the one the
menu app carries (app/build.sh). CI runs the same Python, so the import vectors it checks are the Mac's."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ci_runs_the_python_the_menu_app_carries():
    bundled = re.search(r"(?m)^PY_VERSION=([\d.]+)", (ROOT / "app/build.sh").read_text(encoding="utf-8"))
    ci = re.search(r'python-version:\s*"([\d.]+)"', (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert bundled and ci and bundled.group(1) == ci.group(1)
