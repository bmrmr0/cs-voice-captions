"""Speaker identification ("who said it") for the teammate audio stream.

Counter-Strike mixes every teammate's voice into one output stream, so we can't
know *which player* spoke from the audio alone. What we CAN do is tell distinct
*voices* apart: each spoken phrase is turned into a small voice-fingerprint
(embedding) with Resemblyzer, and we cluster them online. The first new voice
becomes "P1" with one colour, the next distinct voice "P2" with another, etc.

This is optional. If Resemblyzer/torch aren't installed the app still runs --
it just labels everyone "TEAM". Real in-game names are deliberately NOT read
from the game (that's the bannable thing we avoid); screen-OCR of the talking
indicator is a possible future add-on.
"""
import numpy as np

# Distinct, readable colours for the speaker dots.
PALETTE = [
    "#7CFC7C",  # green
    "#FF6B6B",  # red
    "#FFD93D",  # yellow
    "#6BCBFF",  # blue
    "#C77DFF",  # purple
    "#FF9F45",  # orange
    "#4DD0E1",  # teal
    "#F06292",  # pink
]


def _normalize(vec):
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


class SpeakerIdentifier:
    def __init__(self, dcfg):
        dcfg = dcfg or {}
        self.enabled = dcfg.get("enabled", True)
        # Defaults mirror config.DEFAULTS["diarization"] so a missing key
        # behaves the same as the shipped config.
        self.threshold = dcfg.get("similarity_threshold", 0.6)
        self.max_speakers = dcfg.get("max_speakers", 5)
        self.encoder = None
        self.speakers = []   # [{centroid, count, label, color}]

        if not self.enabled:
            return
        try:
            from resemblyzer import VoiceEncoder
            self.encoder = VoiceEncoder(verbose=False)
            print("[diarize] speaker identification enabled")
        except Exception as e:  # noqa: BLE001
            self.enabled = False
            print(f"[diarize] disabled ({e}). "
                  "Install requirements-diarization.txt to label individual speakers.")

    def _embed(self, audio_f32):
        from resemblyzer import preprocess_wav
        try:
            wav = preprocess_wav(audio_f32, source_sr=16000)
        except Exception:  # noqa: BLE001
            wav = audio_f32
        if wav is None or len(wav) < int(0.4 * 16000):
            # Too short to fingerprint reliably -- use the raw utterance.
            wav = audio_f32
        if len(wav) < int(0.25 * 16000):
            return None
        return self.encoder.embed_utterance(wav)

    def identify(self, audio_f32):
        """Returns (label, color) or None if identification isn't available."""
        if not self.enabled or self.encoder is None:
            return None
        try:
            emb = self._embed(audio_f32)
        except Exception as e:  # noqa: BLE001
            print(f"[diarize] embed failed: {e}")
            return None
        if emb is None:
            return None

        best_i, best_sim = -1, -1.0
        for i, sp in enumerate(self.speakers):
            sim = float(np.dot(emb, sp["centroid"]))   # both L2-normalized => cosine
            if sim > best_sim:
                best_sim, best_i = sim, i

        if best_i >= 0 and best_sim >= self.threshold:
            sp = self.speakers[best_i]
            sp["centroid"] = _normalize(sp["centroid"] * sp["count"] + emb)
            sp["count"] += 1
            return sp["label"], sp["color"]

        if len(self.speakers) >= self.max_speakers:
            if best_i >= 0:   # out of slots: fold into the closest existing voice
                sp = self.speakers[best_i]
                return sp["label"], sp["color"]
            return None

        idx = len(self.speakers)
        label = f"P{idx + 1}"
        color = PALETTE[idx % len(PALETTE)]
        self.speakers.append(
            {"centroid": _normalize(emb), "count": 1, "label": label, "color": color}
        )
        return label, color
