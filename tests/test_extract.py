import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from extract_text import build_offset_map, split_sentences  # noqa: E402


def sents(text):
    return [text[a:b] for a, b in split_sentences(text)]


def test_basic_split():
    assert sents("Он пришёл. Она ушла! Кто это? Никто.") == ["Он пришёл.", "Она ушла!", "Кто это?", "Никто."]


def test_dialogue_dash_and_quotes():
    text = "– Вот именно… – сказала Мара. «Ну да?» – спросил я."
    assert sents(text) == ["– Вот именно…", "– сказала Мара.", "«Ну да?»", "– спросил я."]


def test_abbreviation_not_split():
    assert sents("Это было в 1990 г. в Москве. Потом т. е. позже.") == ["Это было в 1990 г. в Москве.", "Потом т. е. позже."]


def test_ellipsis_lowercase_continuation():
    assert sents("Он думал… и молчал. Потом ушёл.") == ["Он думал… и молчал.", "Потом ушёл."]


def test_no_terminal_punctuation():
    assert sents("Заголовок без точки") == ["Заголовок без точки"]


def test_offset_map_collapses_whitespace():
    old = "a  b\n\tc "
    new = "a b c"
    m = build_offset_map(old, new)
    assert new[m[0]] == "a" and new[m[3]] == "b" and new[m[6]] == "c"
    assert m[len(old)] == len(new)


def test_offset_map_keeps_leading_indent():
    old = "   x  y"
    new = "   x y"
    m = build_offset_map(old, new)
    assert new[m[3]] == "x" and new[m[6]] == "y"
