# Changelog

## Unreleased

- **Reminder about `-condebug`.** Text chat only works when CS2 is started
  with the `-condebug` launch option, and nothing in the game says so. The
  app now reads CS2's launch options from Steam on start and, if the option
  is missing, explains how to add it (with a button that copies it). It
  keeps reminding until it's added or you tick "Don't remind me again"; if
  it can't tell, it shows once. It checks the Steam account you're signed
  in with, not every account that has used the PC.
- **Reply in chat** (`Ctrl+Alt+R`, or the tray menu). Type English, pick the
  language, press Enter: the translation is copied to the clipboard to paste
  into the game chat. Nothing is typed into the game for you.
- **Mute a player.** Right-click their name in the captions window to hide
  their chat; unmute them in Settings.
- **New version notice.** On start the app checks GitHub's latest release
  and, if it's newer, says so in the tray with a link. Nothing is downloaded.
  Can be turned off in Settings.
- **Start with Windows**, in Settings.
- **Save transcripts** is now in Settings, and starts or stops at once.
- **Overlay position presets** (top or bottom; left, centre or right). At the
  bottom of the screen the overlay grows upwards as captions arrive.
- An issue form for suggesting slang dictionary entries.
- **The overlay is on by default**, with a switch for it in Settings. It
  was off until turned on from the tray, which nothing pointed out.
- **Hotkeys are shown** beside their items in the tray menu, in a Hotkeys
  section in Settings, and with what each does in the startup message.
- **Settings in two columns**, so the window fits on a laptop screen.
- **Polish is captioned by default** too, alongside Russian and Ukrainian.
- **Smaller exe: 145 MB instead of 200 MB**, and less to unpack on every
  start. Dropped what the app never loads: OpenVINO's GPU plugin, importers
  for model formats it doesn't read, onnxruntime, Qt's QML/Quick/PDF parts
  and its translations. The GPU can no longer be chosen with `stt.device`;
  it was never used by default, since it's busy running the game.
- **A lone slang word gets its meaning.** A message that is nothing but one
  dictionary term now shows the term's meaning as the caption. Before, the
  translator often just transliterated an insult, and the meaning was only
  in the note underneath.
- **Far fewer Russian clips lost.** The NPU's guess at a clip's language
  turned out to depend on the clips before it, so real Russian was often
  taken for something else and dropped. The language is now decided on the
  CPU and the NPU is told it. On 121 saved clips this captions all 21
  Russian ones, where 1.0.0 captioned 4. The `translation.verify_language`
  option is gone: the CPU check it switched on is now how the language is
  chosen.
- **Translation quality: Better**, in Settings, switches to the larger
  speech model: somewhat better translations, about three times slower,
  1.5 GB to download once. Applies at the next start.

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
