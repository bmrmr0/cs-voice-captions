# CS Voice Captions

Live captions and translation for Counter-Strike 2 — reads your teammates'
**voice chat** and **text chat**, translates foreign languages, and shows the
result in a window or as an overlay on top of the game.

Built to be anti-cheat safe: it never reads game memory, never injects into or
hooks the game process, and never sends input to it. It only uses Windows
audio APIs and the log file CS2 writes itself.

---

## What it does

| Pipeline | Source | How |
| --- | --- | --- |
| **Voice** | CS2's audio output | Per-process WASAPI loopback captures only `cs2.exe`, so Spotify and Discord don't leak into your captions. A neural voice detector (Silero) picks actual speech out of the gunfire, then Whisper transcribes and translates it **on the NPU**. |
| **Text chat** | `console.log` | CS2 writes this itself with the `-condebug` launch option. We tail the file and translate what shows up. |
| **Speakers** | voice fingerprints | Optional. CS2 mixes all teammates into one stream, so real names aren't available — but distinct *voices* can be told apart and get stable labels (P1, P2, …) with their own colours. |

Everything runs locally except text-chat translation, which uses Google
Translate by default (in-match chat is public; nothing private leaves your
machine). Set `chat.translator` to `"off"` if you'd rather it didn't.

### Telling a teammate apart from a gunfight

The app is fed *all* of CS2's audio, so the hard part is not transcription — it
is deciding what counts as someone talking. A conventional voice detector marks
gunfire, footsteps and the music kit as speech more or less continuously, and
Whisper will confidently invent a sentence for every one of them. Silero, a
small neural detector, separates the two cleanly — measured on this pipeline:

| Input | Peak score | Frames called speech |
| --- | --- | --- |
| Speech | **1.000** | 79% |
| Gunfire-like bursts | 0.062 | 0% |
| White noise, loud | 0.035 | 0% |
| Pure tone | 0.002 | 0% |
| Silence | 0.009 | 0% |

It runs on the CPU in about a millisecond per frame while the NPU handles
Whisper.

On top of that, captions must be a phrase of at least `vad.min_utterance_s`
(3 seconds by default), must not repeat a line shown in the last 20 captions
(which is what music kits do every round), and — with `only_foreign` on — must
not already be English.

### Captions that don't cost you frames

Whisper runs through OpenVINO, so on a machine with an Intel NPU ("AI Boost",
Core Ultra and newer) transcription happens on the **NPU** — which means it
never touches the GPU that is busy running Counter-Strike. On a Core Ultra 9
this is also simply faster than the CPU:

| Device | Inference, 10s of audio |
| --- | --- |
| NPU | **0.40s** |
| CPU | 2.19s |

Without an NPU it falls back to the CPU. The GPU is never selected
automatically — on a gaming machine that is the part running the game. Set
`stt.device` to `"GPU"` if you want it anyway.

---

## Install

### Run the release

1. Download `CSVoiceCaptions.exe` from [Releases](../../releases).
2. Run it. That's the whole install — one file, nothing beside it. It lives in
   the system tray.
3. First launch downloads the speech model and compiles it for your hardware.
   Expect a couple of minutes; every launch after that is a few seconds.

Everything the app needs at runtime — the model, your settings, the compiled
device cache, transcripts — lives in `%LOCALAPPDATA%\CSVoiceCaptions`. Delete
that folder to reset the app completely. Put a file named `portable.txt` next
to the exe to keep it all in the exe's own folder instead, for a USB stick.

### Run from source

```bash
git clone https://github.com/bmrmr0/cs-voice-captions.git
```

```bash
cd cs-voice-captions && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\python main.py
```

Optional extras:

```bash
.venv\Scripts\pip install -r requirements-diarization.txt
```

That enables per-speaker labels and colours (pulls in torch, so it is a large
download). Without it everyone is just labelled `TEAM`.

---

## Set up CS2

1. **Text chat** — add `-condebug` to CS2's launch options
   (Steam → right-click Counter-Strike 2 → Properties → Launch Options).
   Without it CS2 writes no `console.log` and text chat won't appear.
2. **Overlay** — run CS2 in **Fullscreen Windowed**. Exclusive fullscreen
   paints over every other window, including this one.

The captions window works either way; only the on-game overlay needs windowed
mode.

---

## Using it

The tray icon is the control panel — green when listening, grey when paused:

- **Pause / resume listening**
- **Show / hide game overlay**
- **Unlock overlay to move it** — the overlay is click-through by default so
  the mouse reaches the game. Unlock it, drag it where you want, lock it
  again. The position is saved for next time.
- **Show captions window**
- **Quit**

Default global hotkeys, which work while CS2 has focus:

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+P` | pause / resume listening |
| `Ctrl+Alt+O` | show / hide the game overlay |
| `Ctrl+Alt+C` | bring up the captions window |

Rebind them under `hotkeys` in your settings file. A combination needs at least one
modifier; if another program already owns it, the app says so at startup and
carries on without it.

---

## Configuration

The exe ships with **no config file** — every default is compiled in, so a bare
executable is fully configured. To change something, drop a `config.json` into
`%LOCALAPPDATA%\CSVoiceCaptions` (or the repo root when running from source)
containing only the keys you want to override. The app also writes that file
itself when you move the overlay.

### The settings that matter most

| Key | Default | What it does |
| --- | --- | --- |
| `sources.teammates` | `true` | Caption CS2's audio. |
| `sources.my_mic` | `false` | Also caption your own microphone. |
| `capture.teammates_backend` | `"process"` | `"process"` captures only CS2. `"loopback"` captures the whole desktop. |
| `stt.device` | `"auto"` | NPU if the machine has one, else CPU. Force it with `"NPU"`, `"CPU"`, `"GPU"`, or an exact name like `"GPU.1"`. |
| `stt.model` | `"small"` | `tiny`, `base`, `small`, `medium`, `large-v3-turbo`. Bigger is more accurate and slower. |
| `stt.beam_size` | `1` | Raise to 5 for accuracy at the cost of speed. |
| `translation.only_foreign` | `true` | Show only foreign speech and hide English — you can already understand your English teammates. |
| `translation.show_original` | `false` | Also show the untranslated text in grey. |
| `vad.aggressiveness` | `2` | 0–3. Higher ignores more background noise but may clip quiet speech. |
| `vad.min_utterance_s` | `3.0` | Only translate phrases at least this long. Short blurts are where Whisper hallucinates most — but most CS2 callouts are 1–2 seconds, so this filters out real ones too. Set `0` to caption everything. |
| `vad.backend` | `"silero"` | Neural voice detection. `"webrtc"` is the old detector and cannot tell the game apart from a person — see below. |
| `vad.speech_threshold` | `0.5` | Silero confidence needed to call something speech. Raise toward `0.7` if game audio still gets through. |
| `vad.min_speech_ratio` | `0.25` | How much of a clip must actually be speech. |
| `vad.repeat_window` | `20` | Suppress a caption identical to one of the last N. Catches music-kit vocals, which sing the same line every round. |
| `vad.blocklist` | `[]` | Regular expressions to ignore outright, e.g. `["let the bodies hit the floor"]`. |
| `overlay.enabled` | `false` | Start with the on-game overlay showing. |
| `transcript.enabled` | `false` | Write every caption to a text file you can read after the match. |

### A note on output language

Whisper's translate task only ever outputs **English** — that is a limit of the
model, not of this app. Foreign voice chat becomes English captions. Text chat
can go to another language via `translation.target_language`, since that path
uses Google Translate rather than Whisper.

---

## Building the exe

```bash
.venv\Scripts\pip install -r requirements.txt -r requirements-build.txt
```

```bash
.venv\Scripts\python build.py
```

Output lands in `dist/`. `build.py --console` keeps a console window open,
which is the fastest way to see why something isn't working.

---

## Troubleshooting

**No captions at all, and the log says clips were "skipped (detected en)".**
You have turned `translation.only_foreign` on. Whisper guesses "en" on short,
noisy clips, so that setting discards far more than it should — an earlier
build shipped with it on and threw away 95% of its captions. Set it back to
`false`.

**"per-process capture unavailable" / "falling back to desktop loopback".**
`proc-tap` couldn't start, so the app switched to capturing the whole desktop —
captions still work, but other apps' audio may be captioned too. Set
`capture.fallback_to_loopback` to `false` if you'd rather it captured nothing
than the wrong thing.

**Text chat never appears.** `-condebug` is missing from CS2's launch options,
or CS2 lives in a Steam library the auto-detect didn't find — set
`chat.console_log_path` to the full path of `console.log`.

**The overlay is invisible in game.** CS2 is in exclusive fullscreen. Switch it
to Fullscreen Windowed.

**Captions lag behind the round.** Use a smaller `stt.model` (`base` or
`tiny`), or make sure it is running on the NPU — the startup line in the log
says which device it picked.

**Sounds that aren't speech become captions.** Whisper will invent a whole
sentence out of gunfire if something lets it through. Raise
`vad.speech_threshold` toward `0.7`. If the log says the VAD backend is
`webrtc` rather than `silero`, that is the actual problem — see below.

**Music kit vocals appear as captions.** They should be caught automatically,
since `vad.repeat_window` suppresses anything already shown recently and a kit
sings the same line every round. If a specific line keeps getting through, add
it to `vad.blocklist`.

**Short callouts never appear.** `vad.min_utterance_s` defaults to `3.0`, and a
lot of real CS2 comms ("one A", "he's low") are shorter than that. Lower it to
`1.5`, or `0` to caption everything. This is not hypothetical: a single spoken
sentence with a pause in the middle splits into two phrases, and the shorter
half gets dropped. The log says exactly which rule discarded it.

**Working out why a clip was discarded.** Every gate now explains itself, so
the log names the rule and the number it wanted:

```
[audio:teammates] clip dropped: phrase ran 2.5s (vad.min_utterance_s wants 3.0s)
[audio:teammates] clip dropped: only 18% of the clip was speech (vad.min_speech_ratio wants 25%)
```

If a whole minute passes with nothing captioned, it reports what it did hear,
which separates "the game was quiet" from "the detector is too strict" from
"no audio ever arrived":

```
[audio:teammates] nothing captioned in the last minute — loudest level 0.412, best speech score 0.06 (needs 0.5)
```

A high level with a low speech score means real sound is arriving but nothing
in it looks like a person talking.

**First launch takes minutes.** It is downloading the model and compiling it
for your NPU. Both are one-time; the compiled result is cached and later
launches take seconds.

---

## How this stays VAC-safe

- Audio comes from the Windows audio session APIs, the same ones OBS and
  Discord use.
- Text chat comes from `console.log`, which the game writes itself when you
  pass Valve's own `-condebug` option.
- Hotkeys use `RegisterHotKey`, the documented way for a desktop app to claim a
  shortcut. No keyboard hook, no input sent to the game.
- Speech recognition runs on your own NPU or CPU. No audio ever leaves the
  machine.
- The overlay is an ordinary always-on-top window. Nothing is drawn inside the
  game's process.

No memory reads, no injection, no DLLs, no driver. Player names for voice chat
are deliberately *not* recovered from the game — that's the part that would
cross the line — which is why voices are labelled P1/P2 instead.

Use at your own risk: no third-party tool can be guaranteed against a future
anti-cheat policy change.

---

## Licence

MIT — see [LICENSE](LICENSE).
