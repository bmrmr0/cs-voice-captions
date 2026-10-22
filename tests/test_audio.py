import numpy as np
import pytest

import audio


def _stream(rate, x, sizes):
    r = audio._Resampler(rate)
    out, i, k = [], 0, 0
    while i < len(x):
        n = sizes[k % len(sizes)]
        out.append(r.process(x[i:i + n]))
        i, k = i + n, k + 1
    return np.concatenate(out)


@pytest.mark.parametrize("rate", [48000, 44100, 32000])
def test_chunking_does_not_change_the_result(rate):
    x = np.random.default_rng(1).standard_normal(rate).astype(np.float32)
    whole = _stream(rate, x, [len(x)])
    pieces = _stream(rate, x, [97, 1440, 3, 2000])
    assert len(whole) == len(pieces)
    assert abs(len(whole) - len(x) * 16000 / rate) <= 1
    assert np.allclose(whole, pieces, atol=1e-5)


def test_passes_speech_band_and_stops_aliasing():
    t = np.arange(48000) / 48000
    low = _stream(48000, np.sin(2 * np.pi * 1000 * t).astype(np.float32), [1440])
    high = _stream(48000, np.sin(2 * np.pi * 11000 * t).astype(np.float32), [1440])
    steady = slice(200, -200)
    assert 0.65 < np.sqrt(np.mean(low[steady] ** 2)) < 0.75     # ~1/sqrt(2): kept
    assert np.sqrt(np.mean(high[steady] ** 2)) < 0.01           # above 8 kHz: gone


def test_16k_passes_through():
    x = np.ones(320, dtype=np.float32)
    assert np.array_equal(audio._Resampler(16000).process(x), x)


def test_whisper_spectrogram_shape_and_range():
    import lang_id
    t = np.arange(16000) / 16000
    mel = lang_id.log_mel((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    assert mel.shape == (80, 3000) and mel.dtype == np.float32
    # Whisper's scaling: at most 8 log10 units below the peak, then (x+4)/4.
    assert mel.max() - mel.min() <= 2.0 + 1e-6
    # A 440 Hz tone lands in the low mel bands, during the first second only.
    assert mel[:20, :100].mean() > mel[:20, 200:].mean()
    filters = lang_id._mel_filters()
    assert filters.shape == (80, 201) and (filters >= 0).all()
