"""Text translation for CS2 chat.

Backend is chosen by config (`chat.translator`):
  * "google"   -> deep-translator's free Google endpoint, with MyMemory as a
                  fallback. In-match chat is public, so this sends nothing
                  private. (Google is what CSTranslator uses.)
  * "off"      -> no translation.

Google's free endpoint is unofficial, and Google refuses it outright (HTTP 429
on every request, however slowly they are sent) for some connections. When
that happens translations would otherwise just stop, so the chain falls
through to MyMemory, and only shows the original text when both refuse.
"""
import re
import time

_LANG_CODES = {
    "english": "en", "russian": "ru", "ukrainian": "uk", "spanish": "es",
    "portuguese": "pt", "german": "de", "french": "fr", "polish": "pl",
    "turkish": "tr", "italian": "it", "chinese": "zh-CN",
}


def _lang_code(name):
    n = (name or "en").strip().lower()
    return _LANG_CODES.get(n, n)


# Distinctive letters, checked in order. MyMemory has no auto-detect, so the
# fallback needs a source language; script is unambiguous for Cyrillic, Greek,
# Arabic, Hebrew, Thai and CJK, and a few letters exist in only one of the
# common Latin-script languages. Anything else is left untranslated.
_SCRIPT_HINTS = [
    ("uk", r"[іїєґ]"),
    ("ru", r"[а-яё]"),
    ("pl", r"[ąćęłńśźż]"),
    ("tr", r"[ğış]"),
    ("pt", r"[ãõ]"),
    ("es", r"[ñ¿¡]"),
    ("de", r"[äöüß]"),
    ("cs", r"[ěřůčšž]"),
    ("fr", r"[àâçèêëîïôûœ]"),
    ("el", r"[α-ω]"),
    ("ar", r"[؀-ۿ]"),
    ("he", r"[֐-׿]"),
    ("th", r"[฀-๿]"),
    ("ja", r"[぀-ヿ]"),
    ("ko", r"[가-힯]"),
    ("zh-CN", r"[一-鿿]"),
]


# deep-translator's MyMemory wrapper rejects bare ISO codes ("ru") and only
# accepts the regional form ("ru-RU").
_MYMEMORY_CODES = {
    "en": "en-GB", "uk": "uk-UA", "ru": "ru-RU", "pl": "pl-PL", "tr": "tr-TR",
    "pt": "pt-PT", "es": "es-ES", "de": "de-DE", "cs": "cs-CZ", "fr": "fr-FR",
    "it": "it-IT", "el": "el-GR", "ar": "ar-SA", "he": "he-IL", "th": "th-TH",
    "ja": "ja-JP", "ko": "ko-KR", "zh-CN": "zh-CN",
}


def _guess_source(text):
    t = (text or "").lower()
    for code, pattern in _SCRIPT_HINTS:
        if re.search(pattern, t):
            return code
    return None


# MyMemory reports most errors by returning them *as the translation*, in
# capitals, rather than failing the request -- so without this check a quota
# warning would be shown on the overlay as if a player had typed it.
_MYMEMORY_ERRORS = re.compile(
    r"MYMEMORY WARNING|INVALID SOURCE LANGUAGE|INVALID TARGET LANGUAGE|"
    r"LANGPAIR=|QUERY LENGTH LIMIT|PLEASE SELECT TWO DISTINCT LANGUAGES")


class _ChainTranslator:
    """Google first; MyMemory when Google refuses; None when both do."""

    GOOGLE_BACKOFF_S = 600      # after a 429, leave Google alone this long
    LOG_EVERY_S = 300           # repeat a failure message at most this often

    def __init__(self, target):
        self.target = _lang_code(target)
        self._google = None
        self._google_off_until = 0.0
        self._said = {}

    def _note(self, key, msg):
        """Log a failure, but not on every chat line. Unlike a warn-once flag,
        it comes back, so a translator that stays broken stays visible."""
        now = time.monotonic()
        if now - self._said.get(key, -1e9) >= self.LOG_EVERY_S:
            self._said[key] = now
            # Capped: some library errors embed every supported language.
            msg = " ".join(str(msg).split())
            print(f"[translate] {msg[:200]}{'…' if len(msg) > 200 else ''}")

    def _via_google(self, text):
        if time.monotonic() < self._google_off_until:
            return None
        try:
            if self._google is None:
                # Built once and reused: constructing a translator per line
                # re-does deep-translator's setup on every chat message.
                from deep_translator import GoogleTranslator
                self._google = GoogleTranslator(source="auto", target=self.target)
            return self._google.translate(text)
        except Exception as e:  # noqa: BLE001
            self._google = None     # rebuild next time in case the state is bad
            if "TooManyRequests" in type(e).__name__ or "429" in str(e):
                self._google_off_until = time.monotonic() + self.GOOGLE_BACKOFF_S
                self._note("google429",
                           "Google is refusing translations from this "
                           "connection (HTTP 429); using MyMemory instead, "
                           f"retrying Google in {self.GOOGLE_BACKOFF_S // 60} min")
            else:
                self._note("google", f"Google translation failed: {e}")
            return None

    def _via_mymemory(self, text):
        source = _guess_source(text)
        if source is None or source == self.target:
            return None
        src = _MYMEMORY_CODES.get(source)
        dst = _MYMEMORY_CODES.get(self.target)
        if not src or not dst:
            return None
        try:
            from deep_translator import MyMemoryTranslator
            out = MyMemoryTranslator(source=src, target=dst).translate(text)
        except Exception as e:  # noqa: BLE001
            self._note("mymemory", f"MyMemory translation failed: {e}")
            return None
        if not out or _MYMEMORY_ERRORS.search(out):
            self._note("mymemory", f"MyMemory refused: {(out or '').strip()[:120]}")
            return None
        return out

    def translate(self, text):
        return self._via_google(text) or self._via_mymemory(text)


def make_text_translator(cfg):
    """Returns an object with .translate(text) -> str | None, or None if off."""
    ccfg = cfg.get("chat", {})
    tcfg = cfg.get("translation", {})
    if not ccfg.get("translate", True):
        return None
    engine = ccfg.get("translator", "google").lower()
    if engine == "off":
        return None
    return _ChainTranslator(tcfg.get("target_language", "English"))
