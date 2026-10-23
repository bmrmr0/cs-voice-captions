# Contributing

## Before your first commit

```bash
git config core.hooksPath tools/githooks
```

This turns on a check that refuses any commit, or commit message, that would
publish private data: text pasted from CS2's chat log (other players' names
and messages; use made-up examples), names from your own CS2 log, your
machine's paths or name, or anything shaped like a key or token.

## Run from source

```bash
git clone https://github.com/bmrmr0/cs-voice-captions.git
```

```bash
cd cs-voice-captions && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\python main.py
```

Optionally, to tell teammates' voices apart and label them P1, P2, … (pulls
in PyTorch, a large download; not included in the exe):

```bash
.venv\Scripts\pip install -r requirements-diarization.txt
```

## Tests

```bash
.venv\Scripts\pip install pytest
```

```bash
.venv\Scripts\python -m pytest -q tests
```

They run in a second, need no CS2, NPU or network, and run on GitHub for
every push. Use made-up names and messages in them: never real players'.

## Measuring the cost to the game

`tools/perf_record.py` logs once a second, side by side, what the app is doing
(CPU, overlay shown, clips and captions) and how CS2 runs (CPU, GPU, and with
Intel's [PresentMon](https://github.com/GameTechDev/PresentMon) FPS and the
worst frame time). PresentMon needs an administrator prompt.

```bash
.venv\Scripts\python tools\perf_record.py --presentmon path\to\PresentMon.exe
```

To measure without waiting for people to talk, `tools/speech_load.py` runs
saved clips through the app's processing on a schedule, and
`tools/overlay_load.py` shows the overlay with made-up captions; both write
their phases next to the recording.

## Build the exe

```bash
.venv\Scripts\pip install -r requirements.txt -r requirements-build.txt
```

```bash
.venv\Scripts\python build.py
```

The exe lands in `dist/`. Options:

- `--console` keeps a console window open, the quickest way to see why
  something isn't working.
- `--folder` builds the folder version instead: the exe beside its files in
  `dist/CSVoiceCaptions/`. It starts in about 0.4 s instead of 2.4 s,
  because nothing has to be unpacked first.
- `--dist <folder>` builds somewhere else. Windows won't let a running exe be
  replaced, so this lets you build while the app is running and swap the file
  afterwards. A newer build started while an older one runs takes over from it.

## Release

1. Set the version in `version.py` and move the changelog's *Unreleased*
   section under it.
2. Build the release:

   ```bash
   .venv\Scripts\python build.py --release
   ```

   This builds both downloads into `release/`: `CSVoiceCaptions.exe` and
   `CSVoiceCaptions-folder.zip`. It then unpacks every file in each and scans
   it for anything identifying the machine that built it (user name, home and
   AppData paths, the checkout path, machine name, git identity). If anything
   turns up, both are deleted and the build fails. Otherwise it writes
   `SHA256SUMS.txt` for the two.
3. Commit, push, and publish, letting GitHub create the tag on the exact
   commit the exe was built from:

   ```bash
   gh release create v1.2.3 release/CSVoiceCaptions.exe release/CSVoiceCaptions-folder.zip release/SHA256SUMS.txt --target <commit> --title "CS Voice Captions 1.2.3" --notes-file <notes>
   ```

## Layout

| | |
| --- | --- |
| `main.py` | Entry point; starts the capture, transcription and chat workers and the UI. |
| `audio.py` | Captures CS2's audio and cuts it into phrases with the voice detector. |
| `stt.py` | Whisper on the NPU or CPU: transcription, translation and the filters. |
| `chat_log.py` | Reads text chat from `console.log`; finds Steam and CS2's launch options. |
| `translate.py` | Text translation: Google's endpoints, then MyMemory. |
| `glossary.py`, `slang.json` | The slang dictionary. |
| `ui.py`, `settings_window.py` | Captions window, overlay, tray and settings. |
| `config.py` | Defaults and the settings file. |
| `tools/` | Icon generation, the exe leak scan, the commit privacy check. |
