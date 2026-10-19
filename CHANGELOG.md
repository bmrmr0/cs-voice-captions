# Changelog

## Unreleased

- **Reminder about `-condebug`.** Text chat only works when CS2 is started
  with the `-condebug` launch option, and nothing in the game says so. The
  app now reads CS2's launch options from Steam on start and, if the option
  is missing, explains how to add it (with a button that copies it). It
  keeps reminding until it's added or you tick "Don't remind me again"; if
  it can't tell, it shows once.

## 1.0.0

First public release. A single portable `CSVoiceCaptions.exe` — nothing to
install, nothing written beside it.

### Captions
- **Voice chat captions** from CS2's own audio only (not Discord, music or the
  browser), transcribed and translated to English on the **NPU** where there
  is one, so the GPU stays free for the game.
- **Text chat** — all-chat and team chat — read from the log CS2 writes with
  `-condebug`, translated through Google Translate, with MyMemory as a fallback.
  Shows where a teammate was (`[TEAM · B Site]`).
- **Only the languages you pick** are captioned (Russian and Ukrainian by
  default), and English is hidden unless you turn it on.
- **Slang and swearing explained.** Machine translation gets gaming slang
  wrong (`есть!` is "Got him!", not "Yes"); a built-in dictionary fixes what it
  can and adds a short note for the rest. Add your own words from Settings.
- **Fewer junk captions:** a neural voice detector ignores gunfire and music,
  a second check confirms the language before a caption is shown, and CS2's
  own radio lines and Whisper's stock phrases are filtered out.
- **No chat floods:** a player repeating the same line is shown once.

### The app
- Lives in the tray. A **connection indicator** in the window and on the tray
  icon shows whether CS2 is being heard.
- **On-game overlay**, click-through, movable, sized and timed in Settings.
- **Settings window** for everything that's safe to change mid-match, with an
  explanation on hover for each option.
- **Global hotkeys** that work while CS2 has focus (Ctrl+Alt+P / O / C).
- **Reconnects by itself** when CS2 is restarted.
- **One copy at a time**; starting a newer version replaces the running one.

### Anti-cheat
Uses only Windows audio APIs and the log file CS2 writes itself. No memory
reads, no injection, no input sent to the game. See *How this stays VAC-safe*
in the README.

### Requirements
Windows 10 or 11, 64-bit. Capturing CS2's audio alone needs Windows 11 or a
recent Windows 10; older versions fall back to the whole desktop's audio (the
indicator says so). An Intel NPU (Core Ultra) is used if present;
otherwise it runs on the CPU. The first launch downloads the speech model
(a few hundred MB) into `%LOCALAPPDATA%\CSVoiceCaptions`.
