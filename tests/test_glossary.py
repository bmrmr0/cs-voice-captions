import json
import os

import pytest

import glossary


@pytest.fixture
def g(tmp_path):
    data = {
        "ru": {"phrases": {"есть": "Got him!"},
               "terms": {"петух*": "severe insult", "раш": "rush"},
               "replace": {"вх": "wallhack"}},
        "pl": {"phrases": {"dawaj": "Come on!"}, "terms": {"kurw*": "expletive"}},
        "any": {"terms": {"cyka": "bitch"}},
    }
    path = tmp_path / "slang.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return glossary.Glossary(str(path))


def test_whole_phrase_with_repeats_and_punctuation(g):
    assert g.phrase("Есть, есть, есть!", ["ru"]) == "Got him!"
    assert g.phrase("есть кто?", ["ru"]) is None


def test_lone_term_becomes_the_caption(g):
    assert g.match_phrase("петуха!!", ["ru"]) == ("Severe insult", "ru")
    assert g.phrase("ты петух", ["ru"]) is None              # a sentence: note only
    assert g.terms("ты петух", ["ru"]) == [("петух", "severe insult")]


def test_prefix_terms_keep_the_word_as_typed(g):
    assert g.terms("ну и петуха", ["ru"]) == [("петуха", "severe insult")]


def test_uk_and_ru_share_tables(g):
    assert g.phrase("есть", ["uk"]) == "Got him!"


def test_any_table_and_latin_accents(g):
    assert g.terms("cyka blyat", []) == [("cyka", "bitch")]
    assert g.phrase("Dawaj!", ["pl"]) == "Come on!"


def test_pretranslate_swaps_only_listed_words(g):
    assert g.pretranslate("легко с вх играть", ["ru"]) == ("легко с wallhack играть", ["вх"])


def test_user_file_is_merged_over_bundled(tmp_path, g):
    user = tmp_path / "mine.json"
    user.write_text(json.dumps({"ru": {"phrases": {"есть": "Got one!"}}}, ensure_ascii=False),
                    encoding="utf-8")
    merged = glossary.Glossary(g._bundled_path, str(user))
    assert merged.phrase("есть", ["ru"]) == "Got one!"


def test_shipped_dictionary_loads_and_has_each_section():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "slang.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for lang in ("ru", "uk", "pl", "tr", "any"):
        assert data[lang]["terms"], lang
    assert glossary.Glossary(path).phrase("есть", ["ru"])


def test_transliterated_swearing_is_swapped_in_the_english():
    g = glossary.Glossary(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "slang.json"))
    assert g.fix_output("Watch the left, blyat!") == "Watch the left, fuck!"
    assert g.fix_output("Blyat, go nahui") == "Fuck, go fuck yourself"
    assert g.fix_output("Sukiyaki for dinner") == "Sukiyaki for dinner"      # whole words only
    assert g.phrase("ахахах", ["ru"]) == "Haha"
    assert g.phrase("kurwa", ["pl"]) == "Fuck!"
