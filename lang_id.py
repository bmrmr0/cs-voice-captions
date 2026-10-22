"""Which language a clip is in, worked out on the NPU.

OpenVINO GenAI's Whisper pipeline gets this wrong on the NPU: its answer
depends on the clips it heard before (see stt.Transcriber._get_detector).
So the language is asked of the model's two halves directly:

  * the encoder -- the expensive part, ~all of the work -- on the NPU. It
    is stateless: the same audio in, the same result out, whatever came
    before.
  * one step of the decoder on the CPU, from a freshly reset state, reading
    which language token it rates highest. That is how Whisper picks a
    language itself; doing it here means nothing carries over between clips.

The audio-to-spectrogram step Whisper expects (80 log-mel bands over 30 s)
is done here in NumPy, matching the reference implementation.
"""
import json
import os

import numpy as np

SAMPLE_RATE = 16000
N_FFT, HOP, N_MELS, N_SAMPLES = 400, 160, 80, 480000


def _mel_filters(sr=SAMPLE_RATE, n_fft=N_FFT, n_mels=N_MELS):
    """Slaney-style mel filterbank, as librosa.filters.mel builds it (and so
    as Whisper was trained with)."""
    fmin, fmax = 0.0, sr / 2

    def hz_to_mel(f):
        f = np.asarray(f, dtype=np.float64)
        f_sp, min_log_hz, logstep = 200.0 / 3, 1000.0, np.log(6.4) / 27.0
        mel = f / f_sp
        log = f >= min_log_hz
        return np.where(log, min_log_hz / f_sp + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, mel)

    def mel_to_hz(m):
        m = np.asarray(m, dtype=np.float64)
        f_sp, min_log_hz, logstep = 200.0 / 3, 1000.0, np.log(6.4) / 27.0
        min_log_mel = min_log_hz / f_sp
        return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)

    fft_freqs = np.linspace(0, sr / 2, n_fft // 2 + 1)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_freqs[None, :]
    weights = np.maximum(0, np.minimum(-ramps[:-2] / fdiff[:-1, None], ramps[2:] / fdiff[1:, None]))
    weights *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return weights.astype(np.float32)


_FILTERS = None
_WINDOW = np.hanning(N_FFT + 1)[:-1].astype(np.float32)      # periodic Hann


def log_mel(audio):
    """Whisper's input: 80 x 3000 log-mel spectrogram of the clip padded to
    30 s."""
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = _mel_filters()
    x = np.zeros(N_SAMPLES, dtype=np.float32)
    a = np.asarray(audio, dtype=np.float32)[:N_SAMPLES]
    x[:len(a)] = a
    x = np.pad(x, N_FFT // 2, mode="reflect")                  # centred frames
    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP]
    power = np.abs(np.fft.rfft(frames * _WINDOW, axis=1)) ** 2   # (3001, 201)
    mel = _FILTERS @ power[:-1].T                                # (80, 3000)
    log = np.log10(np.maximum(mel, 1e-10))
    log = np.maximum(log, log.max() - 8.0)
    return ((log + 4.0) / 4.0).astype(np.float32)


class LanguageId:
    def __init__(self, model_dir, encoder_device="NPU", cache_dir=None, threads=2):
        import openvino as ov
        core = ov.Core()
        if cache_dir:
            core.set_property({"CACHE_DIR": cache_dir})
        enc = core.read_model(os.path.join(model_dir, "openvino_encoder_model.xml"))
        enc.reshape({enc.inputs[0].get_any_name(): [1, N_MELS, 3000]})   # NPUs want fixed shapes
        self._encoder = core.compile_model(enc, encoder_device).create_infer_request()
        dec = core.read_model(os.path.join(model_dir, "openvino_decoder_model.xml"))
        self._decoder = core.compile_model(dec, "CPU", {"INFERENCE_NUM_THREADS": threads}) \
            .create_infer_request()
        with open(os.path.join(model_dir, "generation_config.json"), encoding="utf-8") as f:
            gen = json.load(f)
        self._sot = int(gen.get("decoder_start_token_id", 50258))
        langs = gen.get("lang_to_id") or {}
        self._codes = [tok.strip("<|>") for tok in langs]
        self._ids = np.array(list(langs.values()), dtype=np.int64)
        self.device = encoder_device

    def detect(self, audio):
        """The clip's language code, e.g. "ru"."""
        self._encoder.infer({0: log_mel(audio)[None]})
        hidden = self._encoder.get_output_tensor(0).data
        self._decoder.reset_state()                 # nothing from the last clip
        self._decoder.infer({"input_ids": np.array([[self._sot]], dtype=np.int64),
                             "encoder_hidden_states": hidden,
                             "beam_idx": np.array([0], dtype=np.int32)})
        logits = self._decoder.get_tensor("logits").data[0, -1]
        return self._codes[int(np.argmax(logits[self._ids]))]
