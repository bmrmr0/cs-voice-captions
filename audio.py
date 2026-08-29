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
import queue
import threading
import time

import numpy as np

SAMPLE_RATE = 16000               # what Whisper / Resemblyzer expect
FRAME_MS = 30                     # webrtcvad accepts 10/20/30 ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 480 samples / frame


class _VAD:
    """webrtcvad if available, else a simple energy (RMS) gate."""

    def __init__(self, aggressiveness=2, energy_threshold=0.012):
        self.backend = "energy"
        self._energy_threshold = energy_threshold
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(int(aggressiveness))
            self.backend = "webrtc"
        except Exception:  # noqa: BLE001
            self._vad = None

    def is_speech(self, frame_f32):
        if self.backend == "webrtc":
            pcm = (np.clip(frame_f32, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            try:
                return self._vad.is_speech(pcm, SAMPLE_RATE)
            except Exception:  # noqa: BLE001
                return False
        rms = float(np.sqrt(np.mean(np.square(frame_f32)) + 1e-12))
        return rms > self._energy_threshold


class _Segmenter:
    """Frames -> complete utterances via 'speech then N silent frames'."""

    def __init__(self, vad, silence_ms, max_utterance_s, min_speech_ms=200,
                 preroll_ms=300, min_speech_ratio=0.25):
        self.vad = vad
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        self.max_frames = max(1, int(max_utterance_s * 1000) // FRAME_MS)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.preroll = max(0, preroll_ms // FRAME_MS)
        self.min_speech_ratio = float(min_speech_ratio)
        self.reset()

    def reset(self):
        self.state = "silence"
        self.voiced = []
        self.pre = []
        self.trailing = 0
        self.speech_count = 0

    def push(self, frame):
        if self.state == "silence":
            self.pre.append(frame)
            if len(self.pre) > self.preroll:
                self.pre.pop(0)
            if self.vad.is_speech(frame):
                self.state = "speech"
                self.voiced = list(self.pre)
                self.voiced.append(frame)
                self.pre = []
                self.trailing = 0
                self.speech_count = 1
            return None

        self.voiced.append(frame)
        if self.vad.is_speech(frame):
            self.trailing = 0
            self.speech_count += 1
        else:
            self.trailing += 1
        if self.trailing >= self.silence_frames or len(self.voiced) >= self.max_frames:
            voiced = self.voiced
            speech, total = self.speech_count, max(1, len(voiced))
            self.reset()
            # Two gates, because Whisper will confidently invent a sentence out
            # of gunfire or a footstep. It needs enough speech in absolute
            # terms, AND the clip has to be mostly speech rather than a moment
            # of voice adrift in six seconds of round noise.
            if speech < self.min_speech_frames:
                return None
            if speech / total < self.min_speech_ratio:
                return None
            return np.concatenate(voiced).astype(np.float32)
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

    def run(self):
        vad = _VAD(self.vad_cfg.get("aggressiveness", 2))
        self._log(f"VAD backend = {vad.backend}")
        seg = _Segmenter(vad,
                         self.vad_cfg.get("silence_ms", 500),
                         self.vad_cfg.get("max_utterance_s", 8),
                         self.vad_cfg.get("min_speech_ms", 200),
                         self.vad_cfg.get("preroll_ms", 300),
                         self.vad_cfg.get("min_speech_ratio", 0.25))
        for frame in self.frames():
            if self.stop_event.is_set():
                break
            if self.paused_event.is_set():
                seg.reset()
                continue
            utt = seg.push(frame)
            if utt is not None:
                self._log(f"heard {len(utt) / SAMPLE_RATE:.1f}s of speech")
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
