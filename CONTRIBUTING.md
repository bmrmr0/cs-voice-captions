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

   This builds into `release/`, then unpacks every file inside the exe and
   scans it for anything identifying the machine that built it (user name,
   home and AppData paths, the checkout path, machine name, git identity). If
   anything turns up, the exe is deleted and the build fails. Otherwise it
   writes `SHA256SUMS.txt`.
3. Commit, push, and publish, letting GitHub create the tag on the exact
   commit the exe was built from:

   ```bash
   gh release create v1.2.3 release/CSVoiceCaptions.exe release/SHA256SUMS.txt --target <commit> --title "CS Voice Captions 1.2.3" --notes-file <notes>
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
