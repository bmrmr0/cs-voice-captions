"""Speech-to-text and translation.

faster-whisper does the heavy lifting. Two translation modes are supported:

  * "whisper"  -> Whisper's built-in translate task turns foreign speech
                  directly into English in a single pass. Best for
                  understanding Russian (or any language) teammates.
  * "lmstudio" -> transcribe in the original language, then send the text to a
                  local model served by LM Studio for translation into any
                  target language.
"""
import glob
import os
import re
import site
import sys

import numpy as np


def _add_cuda_dll_dirs():
    """On Windows, make the pip-installed NVIDIA CUDA/cuDNN DLLs discoverable so
    ctranslate2 can load them for GPU inference. Without this, faster-whisper
    silently can't find cuDNN and GPU mode fails. Must run before ctranslate2
    is imported."""
    if not sys.platform.startswith("win"):
        return
    candidates = []
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: CUDA DLLs are flattened next to the app.
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(base)
        candidates += glob.glob(os.path.join(base, "nvidia", "*", "bin"))
    else:
        roots = set()
        try:
            roots.update(site.getsitepackages())
        except Exception:  # noqa: BLE001
            pass
        try:
            roots.add(site.getusersitepackages())
        except Exception:  # noqa: BLE001
            pass
        for root in roots:
            candidates += glob.glob(os.path.join(root, "nvidia", "*", "bin"))

    found = []
    for d in candidates:
        if os.path.isdir(d):
            found.append(d)
            try:
                os.add_dll_directory(d)
            except Exception:  # noqa: BLE001
                pass
    # Also prepend to PATH so transitive deps (cublas -> cudart, cudnn -> ...)
    # resolve via the standard search order.
    if found:
        os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()


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


_TARGET_CODES = {"english": "en", "russian": "ru", "ukrainian": "uk",
                 "spanish": "es", "portuguese": "pt", "german": "de",
                 "french": "fr", "polish": "pl", "italian": "it", "turkish": "tr"}


def _to_lang_code(name):
    n = (name or "english").strip().lower()
    return _TARGET_CODES.get(n, n[:2])


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
}


def _collapse_repeats(text, max_repeats=2):
    """Whisper loops on near-silence: "Go go go. Go go go. Go go go."

    Collapse any phrase (split on sentence punctuation) repeated more than
    `max_repeats` times in a row down to `max_repeats` copies, so a stuck
    decode does not fill the overlay.
    """
    if not text:
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text)
    out, run, prev = [], 0, None
    for part in parts:
        key = part.strip().lower()
        if key and key == prev:
            run += 1
            if run >= max_repeats:
                continue
        else:
            run = 0
            prev = key
        out.append(part)
    return " ".join(p for p in out if p).strip()


class LMStudio:
    """Minimal OpenAI-compatible client for LM Studio's local server.
    Used for the optional voice-translation engine and for text-chat
    translation."""

    def __init__(self, tcfg):
        lm = tcfg["lmstudio"]
        self.url = lm["base_url"].rstrip("/") + "/chat/completions"
        self.model = lm.get("model", "local-model")
        self.api_key = lm.get("api_key", "lm-studio")
        self.target = tcfg.get("target_language", "English")
        self._warned = False

    def translate(self, text):
        import requests  # imported lazily so the whisper engine needs no extra deps

        system = (
            "You are a real-time translator for Counter-Strike voice callouts. "
            f"Translate the user's message into {self.target}. "
            "Reply with ONLY the translation -- no quotes, no explanations. "
            "Keep it short and natural and preserve gaming callouts."
        )
        try:
            resp = requests.post(
                self.url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 120,
                    "stream": False,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
            resp.raise_for_status()
            out = resp.json()["choices"][0]["message"]["content"].strip()
            return out.strip().strip('"').strip()
        except Exception as e:  # noqa: BLE001
            if not self._warned:
                print(f"[lmstudio] translation unavailable ({e}). "
                      "Is LM Studio's server running with a model loaded?")
                self._warned = True
            return None


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tcfg = cfg["translation"]
        self.min_chars = cfg["vad"].get("min_chars", 2)
        self.beam_size = cfg["stt"].get("beam_size", 5)
        self.only_foreign = self.tcfg.get("only_foreign", True)
        self.only_foreign_min_prob = float(
            self.tcfg.get("only_foreign_min_prob", 0.6))
        self.target_code = _to_lang_code(self.tcfg.get("target_language", "English"))
        self._last_text = ""      # suppress back-to-back identical captions

        self.lm = None
        if self.tcfg.get("engine") == "lmstudio":
            self.lm = LMStudio(self.tcfg)
        elif (self.tcfg.get("engine") == "whisper"
              and self.tcfg.get("target_language", "English").lower() not in ("english", "en")):
            print("[stt] note: the 'whisper' translate engine only outputs English. "
                  "Switch translation.engine to 'lmstudio' for other languages.")

        self._load_models()

    # -- model loading -----------------------------------------------------
    def _cuda_available(self):
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _load_models(self):
        """Load a CPU model (always, as the safe fallback) and -- when a GPU is
        present -- a bigger GPU model. Each utterance is then routed to whichever
        is appropriate: GPU when it has headroom, CPU when the game is maxing the
        GPU, so captions never cost you frames."""
        from faster_whisper import WhisperModel

        scfg = self.cfg["stt"]
        device_pref = scfg.get("device", "auto")
        # The shareable single-file exe ships no CUDA libs -> always CPU.
        if getattr(sys, "frozen", False) and device_pref == "auto":
            device_pref = "cpu"

        threads = scfg.get("cpu_threads", 4)
        cpu_name = scfg.get("cpu_model", scfg.get("model", "small"))
        gpu_name = scfg.get("gpu_model", "medium")
        cpu_ct = scfg.get("cpu_compute_type", "int8")
        gpu_ct = scfg.get("gpu_compute_type", "float16")
        self.gpu_busy_threshold = scfg.get("gpu_busy_threshold", 95)
        self.cpu_model = None
        self.gpu_model = None
        self._nvml = None

        if device_pref != "cuda":
            self.cpu_model = WhisperModel(cpu_name, device="cpu",
                                          compute_type=cpu_ct, cpu_threads=threads)
            print(f"[stt] CPU model '{cpu_name}' ready ({cpu_ct})")

        if device_pref in ("auto", "cuda") and self._cuda_available():
            try:
                self.gpu_model = WhisperModel(gpu_name, device="cuda",
                                              compute_type=gpu_ct)
                self._init_nvml()
                note = (f"used when GPU < {self.gpu_busy_threshold}% busy, else CPU"
                        if self._nvml is not None else "GPU-usage monitor off")
                print(f"[stt] GPU model '{gpu_name}' ready ({note})")
            except Exception as e:  # noqa: BLE001
                print(f"[stt] GPU model unavailable ({e}); using CPU only")
                self.gpu_model = None

        if self.cpu_model is None and self.gpu_model is None:
            self.cpu_model = WhisperModel(cpu_name, device="cpu",
                                          compute_type=cpu_ct, cpu_threads=threads)
            print(f"[stt] CPU model '{cpu_name}' ready (fallback)")

        self.device_summary = ("GPU + CPU fallback" if self.gpu_model and self.cpu_model
                               else "GPU" if self.gpu_model else "CPU")

    def _init_nvml(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self._pynvml = pynvml
            self._nvml = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:  # noqa: BLE001
            self._nvml = None

    def _gpu_busy(self):
        if self._nvml is None:
            return False
        try:
            util = self._pynvml.nvmlDeviceGetUtilizationRates(self._nvml).gpu
            return util > self.gpu_busy_threshold
        except Exception:  # noqa: BLE001
            return False

    def _pick_model(self):
        # GPU when we have one and the game isn't slamming it; otherwise CPU.
        if self.gpu_model is not None and self.cpu_model is not None:
            return self.cpu_model if self._gpu_busy() else self.gpu_model
        return self.gpu_model or self.cpu_model

    # -- transcription -----------------------------------------------------
    def _run_whisper(self, audio, task="transcribe", language=None):
        segments, info = self._pick_model().transcribe(
            audio,
            task=task,
            language=language,
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        parts = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.7:
                continue
            parts.append(seg.text)
        return ("".join(parts).strip(),
                info.language,
                float(getattr(info, "language_probability", 0.0) or 0.0))

    def _clean(self, text, dedupe=False):
        t = _collapse_repeats((text or "").strip())
        if len(t) < self.min_chars:
            return ""
        norm = t.lower().strip(" .!?,…\"'")
        if norm in HALLUCINATIONS:
            return ""
        # Whisper re-emits the same phrase when it is fed near-silence back to
        # back. One callout is information; the same one five times is noise.
        if dedupe:
            if norm == self._last_text:
                return ""
            self._last_text = norm
        return t

    def _is_target_lang(self, lang):
        return (lang or "").strip().lower() == self.target_code

    def _skip_as_target_lang(self, lang, prob):
        """True when this clip is confidently already in the target language.

        Whisper falls back to guessing "en" on short or noisy CS2 voice clips,
        so a low-confidence "en" is not evidence of English. Requiring a
        minimum probability keeps foreign callouts from being silently dropped.
        """
        if not (self.only_foreign and self._is_target_lang(lang)):
            return False
        if prob < self.only_foreign_min_prob:
            print(f"[stt] kept: detected {lang} but only {prob:.0%} confident")
            return False
        return True

    def process(self, source_key, audio):
        """Returns a result dict or None if nothing worth showing."""
        audio = _normalize_audio(audio)
        do_translate = (
            self.tcfg.get("enabled", True)
            and source_key in self.tcfg.get("translate_sources", [])
        )
        engine = self.tcfg.get("engine", "whisper")

        # Fast path: foreign speech -> English in a single Whisper pass.
        if do_translate and engine == "whisper":
            text, lang, prob = self._run_whisper(audio, task="translate")
            # Only show foreign speech: skip anything Whisper is confident is
            # already in the target language (your English friends/teammates,
            # your own mic bleed, "Thank you"-type noise).
            if self._skip_as_target_lang(lang, prob):
                print(f"[stt] skipped (detected {lang} @ {prob:.0%}, not foreign)")
                return None
            text = self._clean(text, dedupe=True)
            if not text:
                return None
            original = None
            if self.tcfg.get("show_original"):
                otext, _, _ = self._run_whisper(audio, task="transcribe",
                                                language=lang)
                original = self._clean(otext)
            return {"source": source_key, "lang": lang, "lang_prob": prob,
                    "text": text, "original": original, "translated": True}

        # Otherwise transcribe in the source language first.
        text, lang, prob = self._run_whisper(audio, task="transcribe")
        text = self._clean(text, dedupe=True)
        if not text:
            return None

        if do_translate and engine == "lmstudio" and self.lm is not None:
            if self._skip_as_target_lang(lang, prob):
                return None
            translated = self.lm.translate(text)
            if translated:
                return {"source": source_key, "lang": lang, "lang_prob": prob,
                        "text": translated, "original": text, "translated": True}
            # LM Studio offline -> show the original so nothing is lost.
            return {"source": source_key, "lang": lang, "lang_prob": prob,
                    "text": text, "original": None, "translated": False,
                    "note": "LM Studio offline"}

        return {"source": source_key, "lang": lang, "lang_prob": prob,
                "text": text, "original": None, "translated": False}
