"""Speech-to-text, running on the NPU when the machine has one.

Whisper runs through OpenVINO GenAI rather than a CPU/CUDA-only runtime, which
buys the thing this app cares about most: an Intel NPU ("AI Boost") does the
transcription without touching the GPU at all, so captions cost the game
nothing. On a Core Ultra the NPU is also simply faster than the CPU at this --
roughly 5x on the `small` model.

Device order is NPU, then CPU. The GPU is never used: on a gaming machine
that is the part running Counter-Strike. The exe doesn't ship OpenVINO's GPU
plugin at all.

Translation is Whisper's own translate task, which turns speech in any language
directly into English in a single pass. It is entirely local -- no server, no
API key, nothing to install alongside.
"""
import os
import re
import sys

import numpy as np


def _add_openvino_dll_dirs():
    """Make the bundled OpenVINO runtime loadable inside a frozen build.

    OpenVINO ships its core, its device plugins (CPU/NPU) and the
    tokenizers extension as loose DLLs that get loaded by name at runtime, not
    linked at import. In a PyInstaller bundle nothing puts their folders on the
    DLL search path, so the NPU plugin is invisible and openvino_tokenizers.dll
    fails with "error 126". Register the directories before OpenVINO is
    imported and both problems go away.
    """
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    found = []
    for rel in (os.path.join("openvino", "libs"),
                os.path.join("openvino_tokenizers", "lib"),
                "openvino_genai",
                "."):
        d = os.path.abspath(os.path.join(base, rel))
        if os.path.isdir(d):
            found.append(d)
            try:
                os.add_dll_directory(d)
            except Exception:  # noqa: BLE001
                pass
    if found:
        os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")


_add_openvino_dll_dirs()

# Short names -> the pre-converted OpenVINO models on HuggingFace. A full repo
# id or a local directory can also be given in stt.model.
MODEL_REPOS = {
    "tiny": "OpenVINO/whisper-tiny-fp16-ov",
    "base": "OpenVINO/whisper-base-fp16-ov",
    "small": "OpenVINO/whisper-small-fp16-ov",
    "medium": "OpenVINO/whisper-medium-fp16-ov",
    "large-v3-turbo": "OpenVINO/whisper-large-v3-turbo-fp16-ov",
}

_TARGET_CODES = {"english": "en", "russian": "ru", "ukrainian": "uk",
                 "spanish": "es", "portuguese": "pt", "german": "de",
                 "french": "fr", "polish": "pl", "italian": "it", "turkish": "tr"}


def _to_lang_code(name):
    n = (name or "english").strip().lower()
    return _TARGET_CODES.get(n, n[:2])


def _normalize_audio(audio, target_peak=0.95):
    """Peak-normalise an utterance. CS2 voice chat is quiet and compressed, and
    Whisper detects the language / transcribes more reliably at a consistent,
    healthy level."""
    if audio is None or not len(audio):
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-4:
        return (audio * (target_peak / peak)).astype(np.float32)
    return audio


# Whisper happily "transcribes" silence/noise into these stock phrases. We
# drop them so the overlay doesn't fill up with garbage between callouts.
HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching",
    "thank you very much", "please subscribe", "subscribe", "you", "bye",
    "okay", "ok", "the", "so", ".", "..", "...",
    "продолжение следует",
    "субтитры сделал dimatorzok",
    "субтитры создавал dimatorzok",
    "редактор субтитров а.семкин корректор а.егорова",
    # The same family after the translate task has turned it into English.
    # Whisper learned these from subtitle credits and video outros, and
    # produces them from noise; Russian ones arrive here already translated.
    "the end", "to be continued", "thank you for your attention",
    "thanks for your attention", "see you next time", "see you soon",
    "i'll see you next time", "bye bye", "bye-bye",
}

# CS2's own radio commands and agent voice lines. They are recorded speech, so
# the voice detector passes them, and on a short clip Whisper can tag them as
# any language -- one evening a hostage-map line went up as Russian. Matched
# against the whole caption only, and limited to the scripted phrasings: the
# generic ones ("go go go", "follow me", "nice shot", "thanks") are said by
# real teammates too, including Russians whose "го го" translates to exactly
# that, so they are deliberately not here.
GAME_VOICE_LINES = {
    "affirmative", "roger that", "enemy spotted", "enemy down", "need backup",
    "sector clear", "i'm in position", "reporting in", "fire in the hole",
    "flashbang", "flashbang out", "throwing flashbang", "throwing flash",
    "throwing smoke", "smoke out", "throwing molotov", "throwing incendiary",
    "throwing grenade", "throwing decoy", "get out of there it's gonna blow",
    "bomb has been planted", "the bomb has been planted",
    "bomb has been defused", "the bomb has been defused",
    "counter-terrorists win", "counter terrorists win", "terrorists win",
    "hostage has been rescued", "the hostage has been rescued",
    "the hostages", "going for the hostages", "i'm going to the hostages",
    "i'm going for the hostages", "taking fire need assistance",
    "storm the front", "regroup team", "stick together team",
    "hold this position", "you take the point", "team report in",
    "get in position and wait for my go", "need a drop",
}

def _norm_phrase(text):
    """Lower-case words only, no punctuation, ё folded into е."""
    t = (text or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[\w'-]+", t))


# Shapes of the same thing that vary too much to list exactly.
HALLUCINATION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(subtitles?|captions?|subtitle editor|corrector)\b.*\b(by|made|created|edited)\b",
    r"\bamara\.org\b",
    r"\bdimatorzok\b",
    r"\bthanks? (you )?for (watching|listening)\b",
    r"\b(like and )?subscribe\b",
)]


def _collapse_runs(items, max_repeats, join):
    out, run, prev = [], 0, None
    for item in items:
        key = item.strip().lower().strip(",.!?…")
        if key and key == prev:
            run += 1
            if run >= max_repeats:
                continue
        else:
            run = 0
            prev = key
        out.append(item)
    return join.join(o for o in out if o.strip()).strip()


def _collapse_repeats(text, max_repeats=2):
    """Whisper loops when it is fed something that is not speech.

    A real capture produced a single caption reading "yeah, yeah, yeah, ..."
    two hundred times over. Collapse runs at both levels -- repeated sentences
    and repeated comma- or space-separated words -- so a stuck decode cannot
    fill the overlay.
    """
    if not text:
        return text
    text = _collapse_runs(re.split(r"(?<=[.!?…])\s+", text), max_repeats, " ")
    text = _collapse_runs(re.split(r",\s*", text), max_repeats, ", ")
    return _collapse_runs(text.split(" "), max_repeats + 1, " ")


def _compile_blocklist(patterns):
    out = []
    for pat in patterns or []:
        try:
            out.append(re.compile(pat, re.IGNORECASE))
        except re.error as e:
            print(f"[stt] ignoring bad blocklist pattern {pat!r}: {e}")
    return out


class Transcriber:
    def __init__(self, cfg, status_cb=None):
        self.cfg = cfg
        self.tcfg = cfg["translation"]
        self.scfg = cfg["stt"]
        self.status_cb = status_cb
        self.min_chars = cfg["vad"].get("min_chars", 2)
        self.beam_size = max(1, int(self.scfg.get("beam_size", 1)))
        self.blocklist = _compile_blocklist(cfg["vad"].get("blocklist", []))
        self.target_code = _to_lang_code(self.tcfg.get("target_language", "English"))
        self._last_text = ""      # suppress back-to-back identical captions
        from collections import deque
        # Music-kit vocals say the same thing every single round, so anything
        # we have already shown recently is almost certainly not a teammate.
        self._recent = deque(maxlen=int(cfg["vad"].get("repeat_window", 20)))
        self.device = "CPU"
        self.device_summary = "CPU"
        self._pipe = None
        self._detector = None          # picks each clip's language (lang_id.py)
        self._detector_failed = False
        self._load()

    # -- setup -------------------------------------------------------------
    def _say(self, msg):
        print(f"[stt] {msg}")
        if self.status_cb:
            try:
                self.status_cb(msg)
            except Exception:  # noqa: BLE001
                pass

    def _pick_device(self):
        """NPU first (it leaves the GPU free for the game), then CPU.

        Never the GPU: on a gaming machine that is the device running
        Counter-Strike, and stealing it would defeat the point of the whole
        design. `stt.device` can force "NPU" or "CPU".
        """
        import openvino as ov

        want = (self.scfg.get("device") or "auto").strip()
        try:
            available = list(ov.Core().available_devices)
        except Exception as e:  # noqa: BLE001
            print(f"[stt] could not enumerate devices ({e}); using CPU")
            return "CPU", ["CPU"]

        if want.lower() != "auto":
            # Honour an exact name ("NPU.0") or a family ("NPU").
            for dev in available:
                if dev.lower() == want.lower() or dev.lower().startswith(want.lower() + "."):
                    return dev, available
            print(f"[stt] requested device {want!r} not present "
                  f"(have {available}); falling back to auto")

        for dev in available:
            if dev.upper().startswith("NPU"):
                return dev, available
        return "CPU", available

    def _model_path(self, name=None):
        """Resolve the model to a local directory, downloading it once into the
        app's data folder if it isn't there yet."""
        import config as config_mod

        name = (name or self.scfg.get("model") or "small").strip()
        if os.path.isdir(name):
            return name

        repo = MODEL_REPOS.get(name.lower(), name)
        target = os.path.join(config_mod.data_dir(), "models", name)
        marker = os.path.join(target, "openvino_encoder_model.xml")
        if os.path.isfile(marker):
            return target      # already here; never touch the network

        # local_dir gives a plain folder of real files. The default HF cache
        # would instead build a blobs/snapshots tree joined by symlinks, and
        # creating a symlink on Windows needs Developer Mode or admin rights --
        # which most people running a downloaded .exe will not have.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from huggingface_hub import snapshot_download

        self._say(f"Downloading the {name} speech model — one time, "
                  "then it runs entirely offline.")
        os.makedirs(target, exist_ok=True)
        snapshot_download(repo, local_dir=target,
                          allow_patterns=["*.xml", "*.bin", "*.json", "*.txt"])
        if not os.path.isfile(marker):
            raise RuntimeError(f"model download incomplete: {target}")
        return target

    def _load(self):
        import openvino_genai as ov_genai
        import config as config_mod

        path = self._model_path()
        self._model_dir = path
        device, available = self._pick_device()

        # Compiling for the NPU takes about a minute the first time. Cache the
        # compiled blob so every later launch is a couple of seconds.
        ov_cache = os.path.join(config_mod.data_dir(), "device-cache")
        os.makedirs(ov_cache, exist_ok=True)

        def build(dev):
            return ov_genai.WhisperPipeline(path, device=dev, CACHE_DIR=ov_cache)

        if not device.upper().startswith("CPU"):
            self._say(f"Preparing {device} — the first run compiles the model "
                      "and takes about a minute.")
        try:
            self._pipe = build(device)
        except Exception as e:  # noqa: BLE001
            print(f"[stt] {device} unavailable ({e}); falling back to CPU")
            device = "CPU"
            self._pipe = build(device)

        self.device = device
        self.device_summary = ("NPU — GPU left free for the game"
                               if device.upper().startswith("NPU") else device)
        print(f"[stt] '{self.scfg.get('model', 'small')}' ready on {device} "
              f"(devices seen: {available})")
        # Load it now, not on the first clip, so that caption isn't held up
        # by the load as well.
        self._get_detector()

    # -- transcription -----------------------------------------------------
    def _run(self, audio, task="transcribe", language=None):
        kwargs = {"task": task, "return_timestamps": False}
        if self.beam_size > 1:
            kwargs["num_beams"] = self.beam_size
        if language:
            kwargs["language"] = f"<|{language}|>"
        result = self._pipe.generate(audio, **kwargs)
        return str(result).strip(), (getattr(result, "language", "") or "").strip()

    def _clean(self, text, dedupe=False):
        t = _collapse_repeats((text or "").strip())
        if len(t) < self.min_chars:
            return ""
        norm = t.lower().strip(" .!?,…\"'")
        if norm in HALLUCINATIONS or any(p.search(t) for p in HALLUCINATION_PATTERNS):
            print(f"[stt] dropped stock Whisper phrase: {t[:60]}")
            return ""
        if _norm_phrase(t) in GAME_VOICE_LINES:
            print(f"[stt] dropped CS2 voice line: {t[:60]}")
            return ""
        # Music-kit vocals and anything else the user has told us to ignore.
        for pat in self.blocklist:
            if pat.search(t):
                print(f"[stt] blocked (matches {pat.pattern!r}): {t[:60]}")
                return ""
        # Whisper re-emits the same phrase when it is fed near-silence back to
        # back. One callout is information; the same one five times is noise.
        if dedupe:
            if norm == self._last_text:
                return ""
            if norm in self._recent:
                print(f"[stt] skipped repeat (music kit / jingle?): {t[:50]}")
                return ""
            self._last_text = norm
            self._recent.append(norm)
        return t

    # Read from the config on every clip rather than cached, so the settings
    # window can change them while the app runs.
    @property
    def only_foreign(self):
        return self.tcfg.get("only_foreign", True)

    @property
    def languages(self):
        return {str(c).strip().lower()
                for c in self.tcfg.get("languages", []) if str(c).strip()}

    def _get_detector(self):
        """What decides each clip's language: the encoder on the NPU plus
        one fresh decoder step on the CPU (lang_id.LanguageId), else a CPU
        copy of the model. The NPU still does the transcribing and
        translating, told which language to use.

        OpenVINO GenAI's pipeline can't be trusted to pick the language on
        the NPU itself: its answer depends on the clips it heard before. The
        same 30 clips run forwards and then backwards got different
        languages on 25 of them (OpenVINO 2026.3 and 2026.4 alike), while the
        CPU build gave the same answer every time. That is what made the NPU
        seem to hear Russian in English, CS2's radio and noise -- and, just
        as often, to miss real Russian. Told the language, its output no
        longer depends on what came before (121 of 121 identical both ways).

        Driving the model's halves directly avoids the bug: the encoder is
        stateless and the decoder state is reset for every clip. On 121
        saved clips it agreed with the CPU copy on every one, in either
        order, for 0.45 CPU-seconds a clip instead of 3.9 (0.46 s instead of
        1.1-1.5 s). What CPU is left is that one decoder step, which reads
        the whole 30 s of encoder output; giving it only the part covering
        the clip changed 9 answers in 121 even with 3 s to spare.

        The CPU copy is the fallback, if the NPU can't take the encoder.
        Smaller CPU models weren't good enough for it: tiny found 14 of the
        21 Russian clips and called 4 others Russian, base 11 and 8; an
        8-bit small missed 2. Not needed at all when the main model is on
        the CPU, which is reliable on its own. With medium as the main
        model, this uses small."""
        if self.device.upper().startswith("CPU"):
            return None
        if self._detector is not None or self._detector_failed:
            return self._detector
        import config as config_mod
        cache = os.path.join(config_mod.data_dir(), "device-cache")
        big = (self.scfg.get("model") or "").strip().lower() in ("medium", "large-v3-turbo")
        path = self._model_path("small") if big else self._model_dir
        try:
            import lang_id
            self._detector = lang_id.LanguageId(path, self.device, cache, threads=2)
            print(f"[stt] language detection ready on {self.device}")
            return self._detector
        except Exception as e:  # noqa: BLE001
            print(f"[stt] language detection on {self.device} unavailable ({e}); "
                  "using a CPU copy of the model")
        try:
            import openvino_genai as ov_genai
            import cpu
            # Not every core. On a CPU with only one kind of core, four: that
            # kept 2.8 cores busy instead of 4.1 for 0.4 s more per clip.
            # Pinned to efficiency cores (cpu.py), eight: 0.94 s a clip,
            # faster than four were on all cores. Same languages either way.
            self._detector = ov_genai.WhisperPipeline(
                path, device="CPU", CACHE_DIR=cache,
                INFERENCE_NUM_THREADS=cpu.detector_threads())
            print("[stt] language detection ready on CPU")
        except Exception as e:  # noqa: BLE001
            self._detector_failed = True
            print(f"[stt] CPU language detection unavailable ({e}); "
                  "the NPU will guess the language itself")
        return self._detector

    def _detect_language(self, audio):
        """The clip's language code, or None to let the main model decide
        (on the CPU it is reliable on its own)."""
        detector = self._get_detector()
        if detector is None:
            return None
        try:
            if hasattr(detector, "detect"):
                return detector.detect(audio) or None
            r = detector.generate(audio, task="translate", max_new_tokens=1)
            return (getattr(r, "language", "") or "").strip().strip("<|>").lower() or None
        except Exception as e:  # noqa: BLE001
            print(f"[stt] language detection failed ({e})")
            return None

    def _is_target_lang(self, lang):
        return (lang or "").strip().lower() == self.target_code

    def _language_unwanted(self, lang, text=""):
        """Why a clip in `lang` isn't shown, or None if it is. English is
        governed only by only_foreign, so "show English" still works when
        the language list names just ru/uk."""
        if self._is_target_lang(lang):
            if self.only_foreign:
                print(f"[stt] skipped, detected {lang or '?'} "
                      f"(translation.only_foreign){': ' + text[:60] if text else ''}")
                return True
            return False
        languages = self.languages
        if languages and (lang or "").lower() not in languages:
            print(f"[stt] skipped, detected {lang or '?'} "
                  f"(translation.languages){': ' + text[:60] if text else ''}")
            return True
        return False

    def process(self, source_key, audio):
        """Returns a result dict or None if nothing worth showing."""
        audio = _normalize_audio(audio)
        do_translate = (
            self.tcfg.get("enabled", True)
            and source_key in self.tcfg.get("translate_sources", [])
        )

        if do_translate:
            # Language first, on the CPU; a clip in a language that isn't
            # shown never reaches the NPU. Already-English speech is not
            # worth showing: you can hear it. (Only safe because Silero keeps
            # game noise out -- with webrtcvad's false positives this filter
            # threw away 95% of the captions.)
            lang = self._detect_language(audio)
            if lang and self._language_unwanted(lang):
                return None
            # One pass: speech in that language straight out as English.
            text, heard = self._run(audio, task="translate", language=lang)
            if not lang:
                lang = (heard or "").strip("<|>").lower()
                if self._language_unwanted(lang, text):
                    return None
            print(f"[stt] detected {lang or '?'}: {text[:60]}")
            original, gloss, from_phrase = None, [], False
            # Slang and swearing the translate task renders word for word
            # ("есть" -> "yes" when it means "got him"). The dictionary works
            # on the original-language text, so that costs one extra pass --
            # only for languages it has entries for, and only on clips that
            # are about to be shown.
            if self.tcfg.get("slang", True):
                import glossary
                g = glossary.get()
                if g.has(lang):
                    native = self._run(audio, task="transcribe", language=lang)[0].strip()
                    meaning = g.phrase(native, [lang])
                    if meaning:
                        print(f"[stt] slang: {native[:40]} -> {meaning}")
                        text, original, from_phrase = meaning, native, True
                    else:
                        gloss = g.terms(native, [lang])
                        if gloss:
                            print("[stt] slang: " + "; ".join(f"{w} = {m}" for w, m in gloss))
                        if self.tcfg.get("show_original"):
                            original = self._clean(native)
            # A dictionary phrase is not repetition noise: "Got him!" turns
            # up many times a match, and the repeat filter (there for music
            # kits) would swallow every one after the first.
            text = self._clean(text, dedupe=not from_phrase)
            if not text:
                return None
            if original is None and self.tcfg.get("show_original"):
                otext, _ = self._run(audio, task="transcribe", language=lang)
                original = self._clean(otext)
            return {"source": source_key, "lang": lang, "text": text,
                    "original": original, "translated": True, "gloss": gloss}

        text, lang = self._run(audio, task="transcribe",
                               language=self._detect_language(audio))
        text = self._clean(text, dedupe=True)
        if not text:
            return None
        return {"source": source_key, "lang": lang, "text": text,
                "original": None, "translated": False}
