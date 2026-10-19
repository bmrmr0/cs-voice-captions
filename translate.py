"""Text translation for CS2 chat.

Backend is chosen by config (`chat.translator`):
  * "google"   -> Google's free translation, through two of its unofficial
                  endpoints, with MyMemory as the last resort. In-match chat
                  is public, so this sends nothing private.
  * "off"      -> no translation.

Google's free endpoints are unofficial and get throttled (HTTP 429), each
separately and sometimes for days. A throttled endpoint is skipped for ten
minutes and the next one tried; MyMemory, whose quality is far lower ("выблядок"
came back as "pancakes"), is used only when every Google endpoint refuses.
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
#
# Some letters belong to several languages, so those rows name every
# candidate, most likely first, and the fallback tries them in turn. Turkish
# chat is often typed without its own letters ("cocugu" for "çocuğu"), leaving
# only a "ç" that French and Portuguese share -- with a single guess it went
# off as French and came back untranslated.
_SCRIPT_HINTS = [
    (("uk",), r"[іїєґ]"),
    (("ru",), r"[а-яё]"),
    (("pl",), r"[ąćęłńśźż]"),
    (("tr",), r"[ğış]"),
    (("pt",), r"[ãõ]"),
    (("es",), r"[ñ¿¡]"),
    (("de",), r"ß"),
    (("cs",), r"[ěřůčšž]"),
    (("de", "tr"), r"[äöü]"),
    (("tr", "fr", "pt"), r"ç"),
    (("fr",), r"[àâèêëîïôûœ]"),
    (("es", "pt", "fr"), r"[áéíóú]"),
    (("el",), r"[α-ω]"),
    (("ar",), r"[؀-ۿ]"),
    (("he",), r"[֐-׿]"),
    (("th",), r"[฀-๿]"),
    (("ja",), r"[぀-ヿ]"),
    (("ko",), r"[가-힯]"),
    (("zh-CN",), r"[一-鿿]"),
]


# deep-translator's MyMemory wrapper rejects bare ISO codes ("ru") and only
# accepts the regional form ("ru-RU").
_MYMEMORY_CODES = {
    "en": "en-GB", "uk": "uk-UA", "ru": "ru-RU", "pl": "pl-PL", "tr": "tr-TR",
    "pt": "pt-PT", "es": "es-ES", "de": "de-DE", "cs": "cs-CZ", "fr": "fr-FR",
    "it": "it-IT", "el": "el-GR", "ar": "ar-SA", "he": "he-IL", "th": "th-TH",
    "ja": "ja-JP", "ko": "ko-KR", "zh-CN": "zh-CN",
}


def _guess_sources(text):
    """Candidate source languages for `text`, most likely first; empty if
    nothing in it identifies a language."""
    t = (text or "").lower()
    for codes, pattern in _SCRIPT_HINTS:
        if re.search(pattern, t):
            return list(codes)
    return []


def _same_text(a, b):
    """True if a "translation" is really just the input handed back, which is
    what MyMemory does when told the wrong source language."""
    norm = lambda s: re.sub(r"[\W_]+", "", (s or "").lower())  # noqa: E731
    return norm(a) == norm(b)


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

    # Google's free translation is reachable through several unofficial
    # endpoints, and they are throttled separately. The one deep-translator
    # scrapes (translate.google.com/m) went on answering HTTP 429 for days
    # while these two answered normally from the same connection -- which is
    # how CSTranslator, side by side on one machine, kept getting Google's
    # "bastards" for "выблядок" while this app fell back to MyMemory and
    # showed "pancakes". Tried in order, each with its own back-off.
    _GOOGLE_ENDPOINTS = (
        ("gtx", "https://translate.googleapis.com/translate_a/single",
         {"client": "gtx", "dt": "t"}),
        ("dict-chrome-ex", "https://clients5.google.com/translate_a/t",
         {"client": "dict-chrome-ex"}),
    )
    _THROTTLED = (403, 429, 503)

    @staticmethod
    def _parse_google(data):
        """Both response shapes: gtx gives [[[chunk, source, ...], ...], ...];
        dict-chrome-ex gives [[text, lang]] (or just [text])."""
        if isinstance(data, str):
            return data
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if isinstance(first, str):
            return first
        if not isinstance(first, list) or not first:
            return None
        if isinstance(first[0], str):            # dict-chrome-ex
            return first[0]
        return "".join(seg[0] for seg in first   # gtx: join the sentence chunks
                       if isinstance(seg, list) and seg and isinstance(seg[0], str))

    def _via_google(self, text):
        import requests
        now = time.monotonic()
        off = self.__dict__.setdefault("_endpoint_off_until", {})
        for name, url, params in self._GOOGLE_ENDPOINTS:
            if now < off.get(name, 0.0):
                continue
            try:
                r = requests.get(url, params={**params, "sl": "auto",
                                              "tl": self.target, "q": text},
                                 timeout=6)
                if r.status_code in self._THROTTLED:
                    off[name] = now + self.GOOGLE_BACKOFF_S
                    self._note(f"google-{name}",
                               f"Google ({name}) is refusing translations "
                               f"(HTTP {r.status_code}); trying the next service, "
                               f"retrying it in {self.GOOGLE_BACKOFF_S // 60} min")
                    continue
                r.raise_for_status()
                out = self._parse_google(r.json())
                if out and out.strip():
                    return out
            except Exception as e:  # noqa: BLE001
                self._note(f"google-{name}", f"Google ({name}) translation failed: {e}")
        return None

    def _via_mymemory(self, text):
        dst = _MYMEMORY_CODES.get(self.target)
        if not dst:
            return None
        from deep_translator import MyMemoryTranslator
        for source in _guess_sources(text):
            src = _MYMEMORY_CODES.get(source)
            if source == self.target or not src:
                continue
            try:
                out = MyMemoryTranslator(source=src, target=dst).translate(text)
            except Exception as e:  # noqa: BLE001
                self._note("mymemory", f"MyMemory translation failed: {e}")
                return None
            if not out or _MYMEMORY_ERRORS.search(out):
                self._note("mymemory", f"MyMemory refused: {(out or '').strip()[:120]}")
                return None
            if not _same_text(out, text):
                return out
            # Handed back unchanged: wrong guess, so try the next candidate.
        return None

    CACHE_SIZE = 500

    def translate(self, text):
        # Chat repeats itself ("gg", "nice", the same insult every round),
        # and each request spends quota on a service that is either
        # rate-limiting this connection (Google) or capped per day (MyMemory).
        # Only successes are cached, so a failure is retried next time.
        key = text.strip()
        cache = self.__dict__.setdefault("_cache", {})
        if key in cache:
            cache[key] = cache.pop(key)          # most recently used goes last
            return cache[key]
        out = self._via_google(text) or self._via_mymemory(text)
        if out:
            cache[key] = out
            if len(cache) > self.CACHE_SIZE:
                cache.pop(next(iter(cache)))     # evict the least recently used
        return out


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
