# Changelog

## 1.1.0 — 2026-10-22

Two downloads now: the single `CSVoiceCaptions.exe` as before, and
`CSVoiceCaptions-folder.zip`, the same app as a folder, which starts in about
0.4 s instead of 2.4 s and is flagged by antivirus tools less often.

### Captions you were missing
- **Far fewer Russian clips lost.** The NPU's guess at a clip's language
  depended on the clips before it, so real Russian was often taken for
  something else and dropped. The language is now worked out separately for
  every clip. On 121 saved clips this captions all 21 Russian ones, where
  1.0.0 captioned 4.
- **Short chat lines no longer go missing from the overlay** when Google
  misjudges their language (two transliterated words of Russian came back
  as Tajik). Where the alphabet points to a language you picked, it wins.
- **Polish typed without Polish letters** ("dzieki", "nie wiem") is
  translated; it used to be taken for English.
- **A lone slang word gets its meaning** as the caption, instead of a
  transliteration with the meaning in a note.
- **More slang:** Polish, Ukrainian and Turkish callouts ("dawaj", "мінус
  два", "helal olsun") and insults. Lines in plain Latin letters are checked
  against the dictionaries of the languages you picked.
- **Polish is captioned by default**, alongside Russian and Ukrainian.
- **Better chat translations of slang**, from reviewing real captions: laughter
  on its own ("ахах") reads "Haha" instead of nonsense, short forms like "мб"
  (maybe) and "кик" (kick) are understood, and a line with slang swapped in is
  translated as the language it is, not left half-untranslated.
- **Swearing is translated, not described or left as is:** insults read as
  what a player would say in English, and transliterated swearing the
  translators leave behind ("blyat", "suka") becomes English in the caption.

### Lighter on the game
- **About a sixth of the CPU per spoken clip** (0.6 CPU-seconds instead of
  3.9), and captions about 0.9 s sooner. Each clip's language is worked out
  with the speech model's encoder on the NPU; only one small step stays on
  the CPU.
- **Off the game's cores.** On CPUs with performance and efficiency cores
  (recent Intel), the app runs on the efficiency cores only, leaving the
  performance cores to CS2.
- **Below-normal priority**, so CS2 always comes first.
- **The voice detector** uses half the CPU it did, and half again while the
  game is silent.
- **Smaller download:** about 114 MB instead of 200 MB. Dropped what the app
  never loads (OpenVINO's GPU plugin and model importers, onnxruntime, parts
  of Qt) and SciPy, replaced by a streaming resampler that also removes a
  seam the old one left in the audio every 30 ms.

### Added
- **Welcome window** on the first start: where captions appear, what CS2
  needs for the overlay (Fullscreen Windowed) and for text chat
  (`-condebug`, with whether it's set), and the hotkeys. Reopen it from the
  tray (Welcome & help).
- **Reminder about `-condebug`** on start while it's missing from CS2's
  launch options, for the Steam account you're signed in with.
- **Reply in chat** (`Ctrl+Alt+R`): type English, and the translation is
  copied to paste into the game chat. It offers the language you last heard.
  Nothing is typed into the game for you.
- **Mute a player:** right-click their name in the captions window.
- **New version notice** in the tray, with a link. Nothing is downloaded.
- **Translation quality: Better** in Settings: the larger speech model,
  somewhat better translations, about three times slower on the NPU.
- **Start with Windows** and **Save transcripts** in Settings.
- **Overlay:** position presets (it follows CS2's monitor and grows upwards
  at the bottom), a background darkness setting, and lines that fade out.
- **Tray → Troubleshooting → Open log / Open data folder**; the previous
  session's log is kept too.
- An issue form for suggesting slang.

### Changed
- **The overlay is on by default**, and shows only what needs translating:
  voice captions and chat translated from a language you picked. English
  chat and status lines stay in the captions window.
- **Hotkeys are shown** in the tray menu, in Settings and on start.
- **Settings in two columns**, so the window fits on a laptop screen.
- **The captions window, Settings and the reply window remember** where you
  left them.
- **"Ignore speech shorter than" defaults to 0.7 s** (was 0.5): of the
  captions from shorter speech in 157 saved clips, 1 in 6 was real.
- `stt.device` takes `"NPU"` or `"CPU"`; the GPU is never used.
- `translation.verify_language` is gone; the language is always checked.

### Fixed
- `Ctrl+Alt+C` shows the captions window over the game, even when it was
  minimized, without taking the mouse or keyboard from CS2; press it again
  to put it away. The welcome window and the `-condebug` reminder don't take
  the focus from the game either. Settings and the reply window open in
  front.

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
