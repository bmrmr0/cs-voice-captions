"""Text translation for CS2 chat.

Backend is chosen by config (`chat.translator`):
  * "google"   -> deep-translator's free Google endpoint. Best quality, instant,
                  auto-detects the source language. In-match chat is public, so
                  this sends nothing private. (This is what CSTranslator uses.)
  * "lmstudio" -> local LLM via LM Studio (needs its Local Server running).
  * "off"      -> no translation.
"""

_LANG_CODES = {
    "english": "en", "russian": "ru", "ukrainian": "uk", "spanish": "es",
    "portuguese": "pt", "german": "de", "french": "fr", "polish": "pl",
    "turkish": "tr", "italian": "it", "chinese": "zh-CN",
}


def _lang_code(name):
    n = (name or "en").strip().lower()
    return _LANG_CODES.get(n, n)


class _GoogleTranslator:
    def __init__(self, target):
        self.target = _lang_code(target)
        self._impl = None
        self._warned = False

    def translate(self, text):
        try:
            if self._impl is None:
                # Built once and reused: constructing a translator per line
                # re-does deep-translator's setup on every chat message.
                from deep_translator import GoogleTranslator
                self._impl = GoogleTranslator(source="auto", target=self.target)
            return self._impl.translate(text)
        except Exception as e:  # noqa: BLE001
            if not self._warned:
                print(f"[translate] Google translation failed: {e}")
                self._warned = True
            self._impl = None       # rebuild next time in case the state is bad
            return None


class _LMStudioTranslator:
    def __init__(self, tcfg):
        from stt import LMStudio
        self.lm = LMStudio(tcfg)

    def translate(self, text):
        return self.lm.translate(text)


def make_text_translator(cfg):
    """Returns an object with .translate(text) -> str | None, or None if off."""
    ccfg = cfg.get("chat", {})
    tcfg = cfg.get("translation", {})
    if not ccfg.get("translate", True):
        return None
    engine = ccfg.get("translator", "google").lower()
    if engine == "off":
        return None
    if engine == "lmstudio":
        return _LMStudioTranslator(tcfg)
    return _GoogleTranslator(tcfg.get("target_language", "English"))
