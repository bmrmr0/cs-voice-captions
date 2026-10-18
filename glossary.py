"""Slang and swear-word dictionary.

Machine translation renders gaming slang word for word, and so gets it wrong:
"есть!" is "got him!", not "yes"; "петух" is a severe insult, not "rooster";
Google turned "ez" into "no" and "prefire" into "he prefers". This fills the
gap with plain data -- slang.json, shipped in the exe -- plus an optional
slang.json of the user's own in the settings folder, merged over it and picked
up while the app runs.

Two kinds of entry, per language:
  * phrases -- when a whole message is nothing but one of these, the caption
    becomes its meaning ("Есть, есть, есть!" -> "Got him!").
  * terms   -- explained in a note under the caption wherever they appear.
    The translation itself is left alone: swapping one word inside machine
    translated text would garble the rest of the sentence.
"""
import json
import os
import re
import threading
import time
import unicodedata

# Accents are stripped for matching in these, since chat is often typed
# without them ("cabron" for "cabrón"). Never for Cyrillic: Unicode splits
# "й" into "и" plus a mark, so stripping would corrupt it.
_LATIN_LANGS = {"pl", "es", "pt", "tr", "fr", "de", "it", "cs", "any"}

# Ukrainian players swear in Russian and the other way round.
_ALIASES = {"uk": ["ru"], "ru": ["uk"]}

_WORDS = re.compile(r"\w+")


def _normalize(text, lang):
    t = (text or "").lower()
    if lang in ("ru", "uk"):
        return t.replace("ё", "е")
    if lang in _LATIN_LANGS:
        t = t.replace("ı", "i")            # Turkish dotless i has no decomposition
        t = "".join(c for c in unicodedata.normalize("NFKD", t)
                    if not unicodedata.combining(c))
    return t


def _words(text, lang):
    return _WORDS.findall(_normalize(text, lang))


class _Table:
    def __init__(self, lang, phrases, terms):
        self.lang = lang
        self.phrases = {}
        for key, meaning in (phrases or {}).items():
            self.phrases[" ".join(_words(key, lang))] = meaning
        entries = []
        for key, meaning in (terms or {}).items():
            prefix = key.rstrip().endswith("*")
            words = _words(key.rstrip().rstrip("*"), lang)
            if words:
                entries.append((words, prefix, meaning))
        # Longest first, so "idi nahui" wins over "nahui" and "хуйн*" over "хуй".
        entries.sort(key=lambda e: (-len(e[0]), -len(" ".join(e[0]))))
        self.terms = entries

    def phrase(self, text):
        words = _words(text, self.lang)
        collapsed = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
        return self.phrases.get(" ".join(collapsed)) if collapsed else None

    def find_terms(self, text):
        words = _words(text, self.lang)
        found, i = [], 0
        while i < len(words):
            for key_words, prefix, meaning in self.terms:
                n = len(key_words)
                window = words[i:i + n]
                if len(window) < n or window[:-1] != key_words[:-1]:
                    continue
                last, want = window[-1], key_words[-1]
                if last == want or (prefix and last.startswith(want)):
                    # The word as it appeared, not the dictionary stem: the
                    # note should say "петуха", not "петух" or "kurw".
                    found.append((" ".join(window), meaning))
                    i += n
                    break
            else:
                i += 1
        return found


class Glossary:
    """Thread-safe: the chat and transcription workers both use one."""

    RELOAD_EVERY_S = 3.0

    def __init__(self, bundled_path, user_path=None):
        self._bundled_path = bundled_path
        self._user_path = user_path
        self._lock = threading.Lock()
        self._tables = {}
        self._user_mtime = None
        self._checked = 0.0
        self._load()

    @staticmethod
    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"[glossary] could not read {path}: {e}")
            return {}

    def _load(self):
        merged = {}
        for source in (self._read(self._bundled_path),
                       self._read(self._user_path) if self._user_path else {}):
            for lang, section in source.items():
                if lang.startswith("_") or not isinstance(section, dict):
                    continue
                into = merged.setdefault(lang, {"phrases": {}, "terms": {}})
                for kind in ("phrases", "terms"):
                    if isinstance(section.get(kind), dict):
                        into[kind].update(section[kind])
        self._tables = {lang: _Table(lang, s["phrases"], s["terms"])
                        for lang, s in merged.items()}
        self._user_mtime = self._mtime()

    def _mtime(self):
        try:
            return os.path.getmtime(self._user_path) if self._user_path else None
        except OSError:
            return None

    def _maybe_reload(self):
        """Pick up edits to the user's own file without a restart."""
        now = time.monotonic()
        if now - self._checked < self.RELOAD_EVERY_S:
            return
        self._checked = now
        if self._mtime() != self._user_mtime:
            self._load()
            print("[glossary] reloaded your slang.json")

    def _tables_for(self, langs):
        seen, out = set(), []
        for lang in list(langs) + [a for l in langs for a in _ALIASES.get(l, [])]:
            if lang not in seen and lang in self._tables:
                seen.add(lang)
                out.append(self._tables[lang])
        return out

    def has(self, lang):
        return bool(self._tables_for([lang]))

    def phrase(self, text, langs):
        """The meaning if `text` is, as a whole, a known phrase; else None."""
        with self._lock:
            self._maybe_reload()
            for table in self._tables_for(langs):
                meaning = table.phrase(text)
                if meaning:
                    return meaning
        return None

    def terms(self, text, langs, limit=3):
        """[(word, meaning), ...] for known terms in `text`. A word matched by
        two tables (Polish "kurw*" and the transliterated "kurwa") is
        explained once, by whichever table is more specific."""
        with self._lock:
            self._maybe_reload()
            out, seen = [], set()
            for table in self._tables_for(list(langs) + ["any"]):
                for word, meaning in table.find_terms(text):
                    if word not in seen:
                        seen.add(word)
                        out.append((word, meaning))
            return out[:limit]


_instance = None
_instance_lock = threading.Lock()


def get():
    """The shared glossary, built on first use."""
    global _instance
    with _instance_lock:
        if _instance is None:
            import config as config_mod
            bundled = None
            for root in (config_mod.bundle_dir(), config_mod.app_dir()):
                candidate = os.path.join(root, "slang.json")
                if os.path.isfile(candidate):
                    bundled = candidate
                    break
            _instance = Glossary(bundled or "",
                                 os.path.join(config_mod.data_dir(), "slang.json"))
        return _instance


def user_file():
    """Path of the user's own dictionary, created from a template if absent."""
    import config as config_mod
    path = os.path.join(config_mod.data_dir(), "slang.json")
    if not os.path.isfile(path):
        template = {
            "_about": ("Your own slang. Same shape as the built-in dictionary: "
                       "per language code, 'phrases' replace a whole caption, "
                       "'terms' are explained under it; a trailing * matches "
                       "any ending. Saved changes apply within a few seconds."),
            "ru": {"phrases": {}, "terms": {}},
            "any": {"phrases": {}, "terms": {}},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
    return path
