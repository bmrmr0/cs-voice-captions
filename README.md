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
| **Voice** | CS2's audio output | Per-process WASAPI loopback captures only `cs2.exe`, so Spotify and Discord don't leak into your captions. Voice activity detection slices it into phrases, then faster-whisper transcribes and translates them. |
| **Text chat** | `console.log` | CS2 writes this itself with the `-condebug` launch option. We tail the file and translate what shows up. |
| **Speakers** | voice fingerprints | Optional. CS2 mixes all teammates into one stream, so real names aren't available — but distinct *voices* can be told apart and get stable labels (P1, P2, …) with their own colours. |

Everything runs locally except text-chat translation, which uses Google
Translate by default (in-match chat is public; nothing private leaves your
machine). Set `chat.translator` to `"lmstudio"` or `"off"` if you'd rather it
didn't.

---

## Install

### Run the release

1. Download `CSVoiceCaptions.exe` and `config.json` from
   [Releases](../../releases) and put them in the same folder.
2. Run the exe. It lives in the system tray.
3. First launch downloads the Whisper model (a few hundred MB) — give it a
   minute.

The released exe runs Whisper on the **CPU**; it ships no CUDA libraries, which
is what keeps it a single shareable file. For GPU speed, run from source.

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

For GPU transcription, also install the CUDA runtime wheels that ctranslate2
needs, and set `stt.device` to `"auto"` or `"cuda"`:

```bash
.venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

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
  again. The position is saved to `config.json`.
- **Show captions window**
- **Quit**

Default global hotkeys, which work while CS2 has focus:

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+P` | pause / resume listening |
| `Ctrl+Alt+O` | show / hide the game overlay |
| `Ctrl+Alt+C` | bring up the captions window |

Rebind them under `hotkeys` in `config.json`. A combination needs at least one
modifier; if another program already owns it, the app says so at startup and
carries on without it.

---

## Configuration

Copy `config.example.json` to `config.json` (next to the exe, or in the repo
root when running from source) and edit. Anything you leave out falls back to
the built-in default, so the file can be as short as you like.

### The settings that matter most

| Key | Default | What it does |
| --- | --- | --- |
| `sources.teammates` | `true` | Caption CS2's audio. |
| `sources.my_mic` | `false` | Also caption your own microphone. |
| `capture.teammates_backend` | `"process"` | `"process"` captures only CS2. `"loopback"` captures the whole desktop. |
| `stt.device` | `"auto"` | `"auto"` uses the GPU when it has headroom and drops to CPU when the game is maxing it, so captions never cost you frames. |
| `stt.cpu_model` / `stt.gpu_model` | `small` / `medium` | Whisper model size. Bigger is more accurate and slower. |
| `translation.only_foreign` | `true` | Show only foreign speech, skipping English teammates. |
| `translation.only_foreign_min_prob` | `0.6` | How sure Whisper must be that a clip is *already* English before it gets skipped. Lower it if English lines slip through; raise it if foreign callouts go missing. |
| `translation.show_original` | `false` | Also show the untranslated text in grey. |
| `vad.aggressiveness` | `2` | 0–3. Higher ignores more background noise but may clip quiet speech. |
| `overlay.enabled` | `false` | Start with the on-game overlay showing. |
| `transcript.enabled` | `false` | Write every caption to `transcripts/captions-<date>.txt`. |

### Translating into something other than English

Whisper's built-in translation only outputs English. For any other target
language, run [LM Studio](https://lmstudio.ai/)'s local server with a model
loaded and set:

```json
{
  "translation": { "engine": "lmstudio", "target_language": "Turkish" },
  "chat": { "translator": "lmstudio" }
}
```

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

**No captions at all, and the log says every clip was "skipped (detected en)".**
Whisper guesses "en" on short, noisy clips. Lower
`translation.only_foreign_min_prob`, or set `translation.only_foreign` to
`false` to see everything.

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

**Captions lag behind the round.** Use a smaller `stt.cpu_model` (`base` or
`tiny`), or lower `stt.beam_size` to `1`.

---

## How this stays VAC-safe

- Audio comes from the Windows audio session APIs, the same ones OBS and
  Discord use.
- Text chat comes from `console.log`, which the game writes itself when you
  pass Valve's own `-condebug` option.
- Hotkeys use `RegisterHotKey`, the documented way for a desktop app to claim a
  shortcut. No keyboard hook, no input sent to the game.
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
