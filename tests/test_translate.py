import translate
from translate import _ChainTranslator, _guess_sources


def test_alphabet_guesses():
    assert _guess_sources("привет") == ["ru"]
    assert _guess_sources("привіт") == ["uk"]
    assert _guess_sources("cześć") == ["pl"]
    assert _guess_sources("çok iyi") == ["tr", "fr", "pt"]


def test_polish_without_its_letters():
    for line in ("dzieki ziom", "nie wiem gdzie on jest", "czekaj na mnie", "dawaj"):
        assert _guess_sources(line) == ["pl"], line


def test_english_chat_is_not_taken_for_polish():
    for line in ("gg wp", "rush b", "what are you doing", "he is low", "nice one",
                 "can you drop me", "eco round", "bomb down a", "thanks bro"):
        assert _guess_sources(line) == [], line


def test_google_response_shapes():
    gtx = [[["Hello ", "Привет ", None], ["world", "мир", None]], None, "ru"]
    assert _ChainTranslator._parse_google(gtx) == "Hello world"
    assert _ChainTranslator._google_source(gtx) == "ru"
    chrome = [["Hello", "pl"]]
    assert _ChainTranslator._parse_google(chrome) == "Hello"
    assert _ChainTranslator._google_source(chrome) == "pl"
    assert _ChainTranslator._parse_google([]) is None


def test_cache_keeps_the_source_language(monkeypatch):
    t = _ChainTranslator("English")
    calls = []

    def fake_google(text):
        calls.append(text)
        t.last_source = "ru"
        return "hello"

    monkeypatch.setattr(t, "_via_google", fake_google)
    assert t.translate("привет") == "hello"
    t.last_source = ""
    assert t.translate("привет") == "hello" and t.last_source == "ru"
    assert calls == ["привет"]                     # second time from the cache


def test_reply_translator_names_its_source():
    t = translate.make_reply_translator({"chat": {}}, "ru")
    assert (t.source, t.target) == ("en", "ru")
    assert translate.make_reply_translator({"chat": {"translator": "off"}}, "ru") is None


def test_source_language_prefers_the_alphabet_over_a_stray_guess():
    pick = translate.pick_source_language
    # Google called two transliterated Russian words Tajik.
    assert pick("tg", ["ru"], ["ru", "uk", "pl"]) == "ru"
    assert pick("ru", ["ru"], ["ru", "uk", "pl"]) == "ru"
    assert pick("de", [], ["ru", "uk", "pl"]) == "de"         # nothing to go on
    assert pick("tg", ["ru"], []) == "tg"                      # any language picked
    assert pick("", ["pl"], ["ru"]) == "pl"                    # no detection at all
