"""Audio capture + voice-activity segmentation.

Capture backends, all of which read audio through standard Windows APIs and
never touch the game process:

  * ProcessSource   -- per-process WASAPI loopback (proc-tap) on cs2.exe, so we
                       capture ONLY Counter-Strike's audio (no Spotify/Discord/
                       browser bleed). This is the default for teammates. If
                       proc-tap is unavailable it degrades to whole-desktop
                       loopback rather than silently capturing nothing.
  * SoundcardSource -- soundcard library; "mic" for your microphone, or
                       "loopback" for whole-desktop audio (fallback backend).

Each source runs in its own thread, slices the stream into spoken utterances
with voice-activity detection, and pushes them onto a shared queue for the STT
worker.
"""
import os
import queue
import re
import threading
import time
import wave

import numpy as np

SAMPLE_RATE = 16000               # what Whisper / Resemblyzer expect
FRAME_MS = 30                     # webrtcvad accepts 10/20/30 ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 480 samples / frame


def _silero_model_path():
    """The bundled Silero VAD model, or the one from the installed package."""
    import config as config_mod

    names = ("silero_vad_op18_ifless.onnx", "silero_vad.onnx")
    for root in (config_mod.bundle_dir(), config_mod.app_dir()):
        for name in names:
            candidate = os.path.join(root, name)
            if os.path.isfile(candidate):
                return candidate
    try:      # running from source
        import silero_vad
        return os.path.join(os.path.dirname(silero_vad.__file__), "data",
                            "silero_vad_op18_ifless.onnx")
    except Exception:  # noqa: BLE001
        return None


class _SileroVAD:
    """Neural voice-activity detection, run through OpenVINO.

    This exists because webrtcvad cannot tell Counter-Strike apart from a
    person. Fed the game's own audio it calls gunfire, footsteps and the music
    kit "speech" continuously, so every utterance ran to the maximum length and
    Whisper invented a sentence for each one. Silero scores those at about
    0.001 and real speech near 1.0.

    Runs on the CPU: the model is ~1 ms per 32 ms chunk, and the NPU is busy
    with Whisper.
    """

    CHUNK = 512          # samples at 16 kHz -- what Silero v5 expects
    # v5 also wants the 64 samples immediately preceding each chunk fed in
    # front of it. This is not optional: without the context the model scores
    # even loud, clean speech at ~0.06 and the gate rejects everything.
    CONTEXT = 64

    def __init__(self, threshold=0.5):
        import openvino as ov

        path = _silero_model_path()
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("silero model not found")
        core = ov.Core()
        self._req = core.compile_model(core.read_model(path), "CPU").create_infer_request()
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._prob = 0.0
        self.threshold = float(threshold)

    def reset(self):
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._prob = 0.0

    @property
    def prob(self):
        """The most recent speech score, 0..1."""
        return self._prob

    def is_speech(self, frame_f32):
        # Frames are 480 samples and Silero wants 512, so buffer across them
        # and keep the most recent score.
        self._buf = np.concatenate([self._buf, frame_f32])
        while len(self._buf) >= self.CHUNK:
            chunk, self._buf = self._buf[:self.CHUNK], self._buf[self.CHUNK:]
            window = np.concatenate([self._context, chunk])
            try:
                out = self._req.infer({"input": window.reshape(1, -1),
                                       "sr": self._sr,
                                       "state": self._state})
                named = {k.get_any_name(): v for k, v in out.items()}
                self._prob = float(np.array(named["output"]).flatten()[0])
                self._state = np.array(named["stateN"], dtype=np.float32)
            except Exception:  # noqa: BLE001
                self._prob = 0.0
            self._context = chunk[-self.CONTEXT:]
        return self._prob >= self.threshold


class _VAD:
    """Silero if we can load it, else webrtcvad, else a plain energy gate."""

    def __init__(self, aggressiveness=2, energy_threshold=0.012,
                 backend="silero", speech_threshold=0.5):
        self.backend = "energy"
        self._energy_threshold = energy_threshold
        self._silero = None
        self._vad = None
        # Last speech score, kept so the capture loop can say what it is
        # hearing when nothing makes it through the gates.
        self.last_prob = 0.0

        if backend == "silero":
            try:
                self._silero = _SileroVAD(speech_threshold)
                self.backend = "silero"
                return
            except Exception as e:  # noqa: BLE001
                print(f"[audio] Silero VAD unavailable ({e}); falling back")

        if backend in ("silero", "webrtc"):
            try:
                import webrtcvad
                self._vad = webrtcvad.Vad(int(aggressiveness))
                self.backend = "webrtc"
            except Exception:  # noqa: BLE001
                self._vad = None

    def reset(self):
        if self._silero is not None:
            self._silero.reset()

    def is_speech(self, frame_f32):
        if self.backend == "silero":
            speech = self._silero.is_speech(frame_f32)
            self.last_prob = self._silero.prob
            return speech
        if self.backend == "webrtc":
            pcm = (np.clip(frame_f32, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            try:
                speech = self._vad.is_speech(pcm, SAMPLE_RATE)
            except Exception:  # noqa: BLE001
                speech = False
            self.last_prob = 1.0 if speech else 0.0
            return speech
        rms = float(np.sqrt(np.mean(np.square(frame_f32)) + 1e-12))
        speech = rms > self._energy_threshold
        self.last_prob = 1.0 if speech else 0.0
        return speech


def _secs(frames):
    return frames * FRAME_MS / 1000.0


class _ClipDump:
    """Writes captured clips to disk so a session can be taken apart offline.

    Off unless `vad.save_clips` is set. Turning it on is the way to find out
    what the gates are actually throwing away -- the discarded clips can be
    listened to, or fed back through the transcriber, instead of guessed at.
    """

    def __init__(self, directory, limit=400):
        os.makedirs(directory, exist_ok=True)
        self.dir = directory
        self.limit = limit
        self.n = 0

    def save(self, mono, kind, reason=""):
        if self.n >= self.limit:
            return
        self.n += 1
        tag = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:44]
        name = f"{self.n:04d}-{_secs(len(mono) // FRAME_SAMPLES):.1f}s-{kind}"
        name += f"-{tag}.wav" if tag else ".wav"
        try:
            with wave.open(os.path.join(self.dir, name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes((np.clip(mono, -1.0, 1.0) * 32767)
                              .astype(np.int16).tobytes())
        except Exception:  # noqa: BLE001
            pass


class _Segmenter:
    """Frames -> complete utterances via 'speech then N silent frames'."""

    def __init__(self, vad, silence_ms, max_utterance_s, min_speech_ms=200,
                 preroll_ms=300, min_speech_ratio=0.25, min_utterance_s=0.0,
                 on_drop=None):
        self.vad = vad
        # Called with a human-readable reason each time a clip is thrown away.
        # Silent discards are how this app previously lost 95% of its captions
        # without saying a word, so every gate now explains itself.
        self.on_drop = on_drop
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        self.max_frames = max(1, int(max_utterance_s * 1000) // FRAME_MS)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.preroll = max(0, preroll_ms // FRAME_MS)
        self.min_speech_ratio = float(min_speech_ratio)
        self.min_phrase_frames = max(0, int(float(min_utterance_s) * 1000) // FRAME_MS)
        self.reset()

    def reset(self):
        self.state = "silence"
        self.voiced = []
        self.pre = []
        self.trailing = 0
        self.speech_count = 0
        self.speech_start = 0     # index in self.voiced where speech begins
        self.last_voiced = -1     # index in self.voiced of the newest voiced frame

    def push(self, frame):
        if self.state == "silence":
            self.pre.append(frame)
            if len(self.pre) > self.preroll:
                self.pre.pop(0)
            if self.vad.is_speech(frame):
                self.state = "speech"
                self.voiced = list(self.pre)
                self.speech_start = len(self.voiced)   # preroll is not phrase
                self.voiced.append(frame)
                self.pre = []
                self.trailing = 0
                self.speech_count = 1
                self.last_voiced = self.speech_start
            return None

        self.voiced.append(frame)
        if self.vad.is_speech(frame):
            self.trailing = 0
            self.speech_count += 1
            self.last_voiced = len(self.voiced) - 1
        else:
            self.trailing += 1
        if self.trailing >= self.silence_frames or len(self.voiced) >= self.max_frames:
            voiced = self.voiced
            speech, total = self.speech_count, max(1, len(voiced))
            # How long the phrase itself ran: first voiced frame to last,
            # ignoring the preroll in front and the silence that ended it.
            # Pauses between words count, because they are part of the phrase.
            phrase = max(0, self.last_voiced - self.speech_start + 1)
            self.reset()
            # Whisper will confidently invent a sentence out of gunfire, so a
            # clip has to clear three gates: enough speech in absolute terms,
            # mostly speech rather than a word adrift in a long noisy clip,
            # and long enough to be a phrase worth translating.
            if speech < self.min_speech_frames:
                return self._drop(
                    f"only {_secs(speech):.1f}s of speech in it "
                    f"(vad.min_speech_ms wants {_secs(self.min_speech_frames):.1f}s)",
                    voiced)
            if speech / total < self.min_speech_ratio:
                return self._drop(
                    f"only {speech / total:.0%} of the clip was speech "
                    f"(vad.min_speech_ratio wants {self.min_speech_ratio:.0%})",
                    voiced)
            if phrase < self.min_phrase_frames:
                return self._drop(
                    f"phrase ran {_secs(phrase):.1f}s "
                    f"(vad.min_utterance_s wants {_secs(self.min_phrase_frames):.1f}s)",
                    voiced)
            return np.concatenate(voiced).astype(np.float32)
        return None

    def _drop(self, reason, voiced=None):
        if self.on_drop is not None:
            self.on_drop(reason, voiced)
        return None


class _Resampler:
    """Streaming resample from an arbitrary source rate to SAMPLE_RATE.

    Uses the exact integer ratio (48k -> 16k is 1/3) with polyphase
    anti-aliasing, carrying a remainder between chunks so no samples are lost
    at chunk boundaries and the filter phase stays continuous.
    """

    def __init__(self, src_rate):
        from math import gcd
        from scipy.signal import resample_poly
        self._rp = resample_poly
        g = gcd(int(src_rate), SAMPLE_RATE)
        self.up = SAMPLE_RATE // g
        self.down = int(src_rate) // g
        self._carry = np.zeros(0, dtype=np.float32)

    def process(self, mono):
        if self.up == 1 and self.down == 1:
            return mono.astype(np.float32)
        buf = np.concatenate([self._carry, mono]) if self._carry.size else mono
        # Keep a whole number of "down" blocks so the phase stays continuous.
        n = (len(buf) // self.down) * self.down
        if n == 0:
            self._carry = buf
            return np.zeros(0, dtype=np.float32)
        usable, self._carry = buf[:n], buf[n:].copy()
        return self._rp(usable, self.up, self.down).astype(np.float32)


class _Framer:
    """Accumulates samples and emits fixed-size FRAME_SAMPLES frames."""

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.float32)

    def push(self, samples):
        self.buf = np.concatenate([self.buf, samples]) if self.buf.size else samples
        out = []
        while len(self.buf) >= FRAME_SAMPLES:
            out.append(self.buf[:FRAME_SAMPLES])
            self.buf = self.buf[FRAME_SAMPLES:]
        return out


def _downmix(arr, channels):
    """Interleaved float32 -> mono float32."""
    if channels <= 1:
        return arr.astype(np.float32)
    usable = (arr.size // channels) * channels
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    return arr[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)


def _soundcard_frames(kind, stop_event, log):
    """Yield 16 kHz mono frames from the default mic or the default speaker's
    loopback. Reconnects with backoff if the device disappears."""
    try:
        import soundcard as sc
    except Exception as e:  # noqa: BLE001
        log(f"soundcard unavailable: {e}")
        return
    # soundcard sets its own 'always' filter on import; override it here so
    # the harmless buffer-discontinuity spam stays out of the console.
    import warnings
    warnings.filterwarnings("ignore", message="data discontinuity")
    backoff = 1
    while not stop_event.is_set():
        try:
            if kind == "loopback":
                spk = sc.default_speaker()
                dev = sc.get_microphone(str(spk.name), include_loopback=True)
            else:
                dev = sc.default_microphone()
            with dev.recorder(samplerate=SAMPLE_RATE, channels=1,
                              blocksize=FRAME_SAMPLES) as rec:
                log(f"capturing via {kind}")
                backoff = 1
                while not stop_event.is_set():
                    data = rec.record(numframes=FRAME_SAMPLES)
                    frame = data[:, 0] if data.ndim > 1 else data
                    frame = np.asarray(frame, dtype=np.float32)
                    if len(frame) != FRAME_SAMPLES:
                        frame = (np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
                                 if len(frame) < FRAME_SAMPLES else frame[:FRAME_SAMPLES])
                    yield frame
        except GeneratorExit:
            return
        except Exception as e:  # noqa: BLE001
            if stop_event.is_set():
                break
            log(f"error: {e}; retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 10)


class _BaseSource(threading.Thread):
    def __init__(self, source_key, out_queue, vad_cfg, stop_event, paused_event,
                 status_cb=None):
        super().__init__(daemon=True, name=f"audio-{source_key}")
        self.source_key = source_key
        self.out = out_queue
        self.vad_cfg = vad_cfg
        self.stop_event = stop_event
        self.paused_event = paused_event
        self.status_cb = status_cb

    def _log(self, msg, to_ui=False):
        print(f"[audio:{self.source_key}] {msg}")
        if to_ui and self.status_cb:
            try:
                self.status_cb(msg)
            except Exception:  # noqa: BLE001
                pass

    def frames(self):
        """Subclasses yield FRAME_SAMPLES-length float32 mono 16 kHz frames."""
        raise NotImplementedError

    def _on_drop(self, why, voiced):
        self._log(f"clip dropped: {why}")
        dump = getattr(self, "_dump", None)
        if dump is not None and voiced:
            dump.save(np.concatenate(voiced), "dropped", why.split(" (")[0])

    def run(self):
        vad = _VAD(self.vad_cfg.get("aggressiveness", 2),
                   backend=self.vad_cfg.get("backend", "silero"),
                   speech_threshold=self.vad_cfg.get("speech_threshold", 0.5))
        self._log(f"VAD backend = {vad.backend}")
        seg = _Segmenter(vad,
                         self.vad_cfg.get("silence_ms", 500),
                         self.vad_cfg.get("max_utterance_s", 8),
                         self.vad_cfg.get("min_speech_ms", 200),
                         self.vad_cfg.get("preroll_ms", 300),
                         self.vad_cfg.get("min_speech_ratio", 0.25),
                         self.vad_cfg.get("min_utterance_s", 3.0),
                         on_drop=lambda why, clip: self._on_drop(why, clip))

        dump = None
        if self.vad_cfg.get("save_clips", False):
            try:
                import config as config_mod
                dump = _ClipDump(os.path.join(config_mod.data_dir(), "clips"))
                self._log(f"saving clips to {dump.dir}")
            except Exception as e:  # noqa: BLE001
                self._log(f"could not open the clip folder: {e}")
        self._dump = dump

        # A minute with nothing captioned is ambiguous: silence in the game,
        # a detector that is too strict, or audio that never arrived at all.
        # Report the loudest and most speech-like thing heard, but only when
        # the minute really was empty, so a working session stays quiet.
        report_every = max(1, 60_000 // FRAME_MS)
        frames_seen = emitted = 0
        peak_level = peak_prob = 0.0

        for frame in self.frames():
            if self.stop_event.is_set():
                break
            if self.paused_event.is_set():
                seg.reset()
                continue

            frames_seen += 1
            level = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
            peak_level = max(peak_level, level)
            utt = seg.push(frame)
            peak_prob = max(peak_prob, vad.last_prob)
            if frames_seen >= report_every:
                if emitted == 0:
                    self._log(f"nothing captioned in the last minute — loudest "
                              f"level {peak_level:.3f}, best speech score "
                              f"{peak_prob:.2f} (needs "
                              f"{self.vad_cfg.get('speech_threshold', 0.5)})")
                frames_seen = emitted = 0
                peak_level = peak_prob = 0.0
            if utt is not None:
                emitted += 1
                self._log(f"heard {len(utt) / SAMPLE_RATE:.1f}s of speech")
                if dump is not None:
                    dump.save(utt, "kept")
                try:
                    self.out.put_nowait((self.source_key, utt))
                except queue.Full:
                    # STT is behind. Drop the oldest clip so live speech wins
                    # over a backlog nobody will still care about.
                    try:
                        self.out.get_nowait()
                        self.out.put_nowait((self.source_key, utt))
                    except (queue.Empty, queue.Full):
                        pass
        if not self.stop_event.is_set():
            self._log("capture stopped, no more audio from this source", to_ui=True)


class SoundcardSource(_BaseSource):
    def __init__(self, source_key, out_queue, vad_cfg, stop_event, paused_event,
                 kind, status_cb=None):
        super().__init__(source_key, out_queue, vad_cfg, stop_event, paused_event,
                         status_cb)
        self.kind = kind   # "mic" or "loopback"

    def frames(self):
        yield from _soundcard_frames(self.kind, self.stop_event, self._log)


class ProcessSource(_BaseSource):
    """CS2-only audio via per-process WASAPI loopback (proc-tap)."""

    def __init__(self, source_key, out_queue, vad_cfg, stop_event, paused_event,
                 process_name, fallback_to_loopback=True, status_cb=None):
        super().__init__(source_key, out_queue, vad_cfg, stop_event, paused_event,
                         status_cb)
        self.process_name = process_name
        self.fallback_to_loopback = fallback_to_loopback

    def _find_pid(self):
        import psutil
        target = self.process_name.lower()
        for p in psutil.process_iter(["name", "pid"]):
            if (p.info.get("name") or "").lower() == target:
                return p.info["pid"]
        return None

    def _fallback(self, reason):
        """Yield desktop-loopback frames so a broken proc-tap degrades to
        'captures too much' rather than 'captures nothing'."""
        if not self.fallback_to_loopback:
            self._log(f"{reason}; set capture.teammates_backend to 'loopback' "
                      "in config.json", to_ui=True)
            return
        self._log(f"{reason} - falling back to desktop loopback "
                  "(other apps' audio may be captioned too)", to_ui=True)
        yield from _soundcard_frames("loopback", self.stop_event, self._log)

    def frames(self):
        try:
            from proctap import ProcessAudioCapture
        except Exception as e:  # noqa: BLE001
            yield from self._fallback(f"per-process capture unavailable ({e})")
            return

        announced = False
        failures = 0
        while not self.stop_event.is_set():
            pid = self._find_pid()
            if pid is None:
                if not announced:
                    self._log(f"waiting for {self.process_name} to start...")
                    announced = True
                time.sleep(2.0)
                continue
            announced = False

            cap = None
            try:
                cap = ProcessAudioCapture(pid=pid)
                cap.start()
                fmt = {}
                try:
                    fmt = cap.get_format() or {}
                except Exception:  # noqa: BLE001
                    pass
                src_rate = int(fmt.get("sample_rate") or 48000)
                channels = int(fmt.get("channels") or 2)
                resampler = _Resampler(src_rate)
                framer = _Framer()
                failures = 0
                self._log(f"capturing {self.process_name} (pid {pid}) at "
                          f"{src_rate} Hz / {channels}ch, game audio only")
                while not self.stop_event.is_set():
                    data = cap.read(timeout=1.0)
                    if not data:
                        if self._find_pid() is None:
                            break          # CS2 closed -> re-wait for it
                        continue
                    arr = np.frombuffer(data, dtype=np.float32)
                    if arr.size == 0:
                        continue
                    mono16 = resampler.process(_downmix(arr, channels))
                    if mono16.size:
                        for fr in framer.push(mono16):
                            yield fr
            except GeneratorExit:
                self._close(cap)
                return
            except Exception as e:  # noqa: BLE001
                if self.stop_event.is_set():
                    break
                failures += 1
                self._close(cap)
                cap = None
                if failures >= 3:
                    # Windows build without process loopback, or the game is
                    # blocking it. Stop retrying and use what we can get.
                    yield from self._fallback(
                        f"per-process capture keeps failing ({e})")
                    return
                self._log(f"capture error: {e}; retrying...")
                time.sleep(2.0)
            finally:
                self._close(cap)

    @staticmethod
    def _close(cap):
        if not cap:
            return
        for fn in ("stop", "close"):
            try:
                getattr(cap, fn)()
            except Exception:  # noqa: BLE001
                pass
