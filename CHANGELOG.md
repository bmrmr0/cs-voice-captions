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
- **A folder version** (`CSVoiceCaptions-folder.zip`) alongside the single
  exe. It starts in 0.4 s instead of 2.4 s, since nothing is unpacked first.
- **Automated tests** (`tests/`), run on GitHub for every push.
- **Chat in a picked language no longer goes missing from the overlay** when
  Google misjudges a short line: two transliterated words of Russian came
  back as Tajik and were hidden. The alphabet now wins in that case.
- **Welcome window on the first start:** where captions appear, what CS2
  needs for the overlay (Fullscreen Windowed) and for text chat
  (`-condebug`, with whether it's set), and the hotkeys. Reopen it from the
  tray menu (Welcome & help).
- **Reply in the language you last heard.** The reply helper picks the
  language of the last foreign voice or chat line; choosing one by hand
  still wins until a different language is heard.
- **The overlay goes on CS2's monitor.** Position presets use the screen CS2
  is on, not always the main one.
- **Overlay background setting** (0–100 %, 0 = no box), and lines fade out
  at the end instead of vanishing.
- **Tray → Troubleshooting → Open log / Open data folder.** The previous
  session's log is kept as `cs-voice-captions.previous.log`.
- **Settings and the reply window remember where you left them.**
- **More slang:** Polish, Ukrainian and Turkish callouts ("dawaj", "мінус
  два", "helal olsun") and insults. Lines typed in plain Latin letters are
  checked against the dictionaries of the languages picked in Settings.
- **Polish typed without Polish letters is translated.** Plain-letter chat
  was taken for English and never sent; common Polish words now give it away.
- **The language step moved to the NPU: about a tenth of the CPU.** The
  model's encoder runs on the NPU and one decoder step, reset for every
  clip, on the CPU. On 121 saved clips: the same language as before on every
  one, 0.45 CPU-seconds a clip instead of 3.9, and captions about twice as
  quick. The CPU copy of the model (1.4 GB) is only a fallback now.
- **The voice detector skips sustained silence** (menus, pauses): half the
  CPU while CS2 is quiet, with identical decisions on 3,799 test frames.
- **"Ignore speech shorter than" defaults to 0.7 s** (was 0.5). On 157 saved
  clips, of the captions in the picked languages under 0.7 s, 1 of 6 was
  real.
- **Off the game's cores.** On CPUs with performance and efficiency cores the
  app runs on the efficiency cores only. Measured on a Core Ultra 9 275HX:
  the language step went from 72% of its work on performance cores to none,
  and captions came 0.5 s sooner (8 efficiency-core threads instead of 4).
- **Less CPU.** The app runs at below-normal priority so CS2 always comes
  first; the voice detector uses one thread (half the constant load) and the
  language step four (2.8 cores busy instead of 4.1).
- **No more SciPy:** resampling CS2's audio is plain NumPy, streaming without
  a seam every 30 ms (the old code restarted its filter on each chunk). It
  matches one-piece resampling exactly, and the exe is about 30 MB smaller.
- **Voice clips are saved only in development builds**; released exes never
  record them by default.
- **The overlay shows only what needs translating:** chat translated from
  a language picked in Settings, and voice captions. English chat, chat in
  other languages and status lines stay in the captions window.
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
