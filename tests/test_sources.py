"""Text sources: OPDS parsing and the grouping of raw hits into editions. No network."""

from __future__ import annotations

from sources import base
from sources.flibusta import Flibusta

ENTRY = """<entry><title>Дао Дэ Цзин</title><author><name>Лао-цзы</name></author>
<dc:language>ru</dc:language>
<content type="text/html">Перевод: Хин-шун Ян&lt;br/&gt;Год издания: 1972&lt;br/&gt;Формат: fb2&lt;br/&gt;Размер: 86 Kb</content>
<link href="/b/70131/fb2" rel="http://opds-spec.org/acquisition/open-access" type="application/fb2+zip" />
</entry>
<entry><title>Дао Дэ Цзин</title><author><name>Лао-цзы</name></author>
<content type="text/html">Перевод: Полежаева Юлия&lt;br/&gt;Формат: rtf&lt;br/&gt;Размер: 75 Kb</content>
<link href="/b/70132/fb2" type="application/fb2+zip" />
</entry>
<entry><title>Троецарствие. Том 1</title><author><name>Ло Гуань-чжун</name></author>
<link href="/b/1/fb2" type="application/fb2+zip" /></entry>
<entry><title>Троецарствие. Том 2</title><author><name>Ло Гуань-чжун</name></author>
<link href="/b/2/fb2" type="application/fb2+zip" /></entry>"""


def test_opds_entries_keep_catalog_facts():
    hits = Flibusta().entries(ENTRY)
    assert [h["translator"] for h in hits[:2]] == ["Хин-шун Ян", "Полежаева Юлия"]
    assert hits[0]["year"] == "1972" and hits[0]["size_kb"] == 86 and hits[0]["kind"] == "fb2"
    assert hits[1]["kind"] == "rtf"  # what the catalog says, not what the link promises
    assert hits[0]["url"] == "https://flibusta.is/b/70131/fb2"


def test_editions_group_volumes_but_not_translations():
    rows = base.editions(Flibusta().entries(ENTRY))
    dao = [r for r in rows if r["title"] == "Дао Дэ Цзин"]
    assert len(dao) == 2  # two translators, two rows
    vols = next(r for r in rows if r["title"] == "Троецарствие")
    assert [p["url"] for p in vols["parts"]] == ["https://flibusta.is/b/1/fb2", "https://flibusta.is/b/2/fb2"]
    assert vols["parts_label"] == "Том 1, Том 2"
