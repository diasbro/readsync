"""Rebuild reader/fonts/*.woff2 from Google Fonts sources: pin variable axes we do not use,
subset to Latin + Cyrillic, convert to woff2, and copy each family's OFL licence.

Run once when adding or updating a font:  make fonts   (needs fonttools and brotli, in [dev])
"""

from __future__ import annotations

import pathlib
import tempfile
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONTS = {  # family directory -> (source file, output name, axes to pin)
    "inter": [
        ("Inter[opsz,wght].ttf", "Inter.woff2", {"opsz": 14}),
        ("Inter-Italic[opsz,wght].ttf", "Inter-Italic.woff2", {"opsz": 14}),
    ],
    "golostext": [("GolosText[wght].ttf", "GolosText.woff2", {})],
    "ibmplexsans": [
        ("IBMPlexSans[wdth,wght].ttf", "IBMPlexSans.woff2", {"wdth": 100}),
        ("IBMPlexSans-Italic[wdth,wght].ttf", "IBMPlexSans-Italic.woff2", {"wdth": 100}),
    ],
    "literata": [
        ("Literata[opsz,wght].ttf", "Literata.woff2", {}),
        ("Literata-Italic[opsz,wght].ttf", "Literata-Italic.woff2", {}),
    ],
    "ptserif": [
        ("PT_Serif-Web-Regular.ttf", "PTSerif.woff2", {}),
        ("PT_Serif-Web-Italic.ttf", "PTSerif-Italic.woff2", {}),
        ("PT_Serif-Web-Bold.ttf", "PTSerif-Bold.woff2", {}),
        ("PT_Serif-Web-BoldItalic.ttf", "PTSerif-BoldItalic.woff2", {}),
    ],
    "merriweather": [
        ("Merriweather[opsz,wdth,wght].ttf", "Merriweather.woff2", {"wdth": 100, "opsz": 18}),
        ("Merriweather-Italic[opsz,wdth,wght].ttf", "Merriweather-Italic.woff2", {"wdth": 100, "opsz": 18}),
    ],
}
UNICODES = "U+0000-024F,U+0400-04FF,U+2000-206F,U+20AC,U+2116,U+2122,U+2190-2199"
OUT = pathlib.Path(__file__).resolve().parent.parent / "reader" / "fonts"


def main() -> None:
    tmp = pathlib.Path(tempfile.mkdtemp())
    (OUT / "licenses").mkdir(parents=True, exist_ok=True)
    for fam, files in FONTS.items():
        (OUT / "licenses" / f"{fam}-OFL.txt").write_bytes(urllib.request.urlopen(BASE + fam + "/OFL.txt").read())
        for src, dst, pins in files:
            raw = tmp / src
            urllib.request.urlretrieve(BASE + fam + "/" + urllib.request.quote(src), raw)
            work = raw
            if pins:
                font = instancer.instantiateVariableFont(TTFont(raw, lazy=False), pins, inplace=False)
                work = tmp / ("pinned-" + src)
                font.save(work)
            font = TTFont(work, lazy=False)
            sub = subset.Subsetter(
                subset.Options(flavor="woff2", layout_features=["*"], notdef_outline=True, hinting=False)
            )
            sub.populate(unicodes=subset.parse_unicodes(UNICODES))
            sub.subset(font)
            font.flavor = "woff2"
            font.save(OUT / dst)
            print(f"{dst:28s} {(OUT / dst).stat().st_size // 1024:5d} KB")


if __name__ == "__main__":
    main()
