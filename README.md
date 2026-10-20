<img src="assets/icon.png" width="96" alt="">

# CS Voice Captions

Live captions and English translation for Counter-Strike 2 **voice chat** and
**text chat**, so you can follow teammates who don't speak your language.
Shown in a window or as an overlay on top of the game.

It runs on your own PC (on the NPU if you have one), reads only the game's
audio and the log file CS2 writes itself, and never touches the game process.

## Features

- **Voice chat captions.** Only CS2's audio is captured, so Discord, music and
  the browser stay out. Speech is transcribed and translated on your PC.
- **Text chat translation** for all-chat and team chat, with where a teammate
  was for team chat.
- **You pick the languages** to translate (Russian and Ukrainian by default).
  English is hidden unless you turn it on.
- **Slang and swearing explained.** `есть!` is "Got him!", not "Yes". You can
  add your own words.
- **Junk filtered out:** gunfire, music kits, CS2's own radio lines and
  repeated chat spam don't become captions.
- **Reply in their language.** Type English, and the translation is copied
  for you to paste into the game chat. Nothing is typed into the game.
- **Mute a player's chat** with a right-click on their name.
- **On-game overlay**, a tray icon that shows whether CS2 is being heard,
  global hotkeys, and a settings window that explains every option.

## Install

1. Download `CSVoiceCaptions.exe` from [Releases](../../releases).
2. **Add `-condebug` to CS2's launch options.** Without it there is no text
   chat. In Steam, right-click Counter-Strike 2 → Properties… → General →
   Launch Options. The app reminds you on start if it's missing.
3. Run the exe. There's nothing else to install; it lives in the system tray.
4. The first launch downloads the speech model and prepares it for your
   hardware, which takes a couple of minutes. Later launches take seconds.

For the overlay, run CS2 in **Fullscreen Windowed**: exclusive fullscreen
draws over every other window.

<details>
<summary><b>Requirements, where files are kept, portable mode</b></summary>

<br>

**Requirements:** Windows 10 or 11, 64-bit. Capturing CS2's audio on its own
needs Windows 11 or a recent Windows 10; older versions fall back to the
whole desktop's audio. An Intel NPU (Core Ultra) is used if there is one,
otherwise the CPU.

**Files:** your settings, the downloaded model and the log live in
`%LOCALAPPDATA%\CSVoiceCaptions`. Delete that folder to reset the app.

**Portable mode:** put a file named `portable.txt` next to the exe and
everything is kept beside it instead, for a USB stick.

**Why `-condebug`?** It makes CS2 write its chat to a log file, which is how
the app reads text chat. It's Valve's own launch option and touches nothing in
the game.

</details>

## Using it

The tray icon's colour, and the indicator at the top of the captions window,
show whether CS2 is being heard:

| | |
| --- | --- |
| 🟢 **Connected to CS2** | Capturing CS2's audio. |
| 🟠 **Waiting for CS2…** | CS2 isn't running yet; the app attaches by itself when it starts. |
| 🟠 **Capturing whole desktop** | CS2-only capture isn't available, so other programs' audio may be captioned too. |
| 🔴 **Not capturing** | Capture stopped; the log says why. |
| ⚪ **Paused** | You paused it. |

<details>
<summary><b>Tray menu, settings and hotkeys</b></summary>

<br>

Right-click the tray icon to pause, show or hide the overlay, unlock the
overlay to drag it into place, show or hide text chat, reply in chat, and
open **Settings** (also a button in the captions window). Hover over any
setting for what it does. Changes apply immediately.

Right-click a player's name in the captions window to mute their chat;
unmute them in Settings.

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+P` | Pause / resume |
| `Ctrl+Alt+O` | Show / hide the overlay |
| `Ctrl+Alt+C` | Bring up the captions window |
| `Ctrl+Alt+R` | Reply in chat: type English, get the translation on the clipboard |

Everything else, including rebinding the hotkeys, is in the
[configuration guide](docs/CONFIGURATION.md).

</details>

## Privacy and anti-cheat

Voice chat is turned into text on your PC and never sent anywhere, and nothing touches the game.

<details>
<summary><b>Details</b></summary>

<br>

- **Voice chat is transcribed and translated locally**, on your NPU or CPU. The audio is never uploaded.
- **Text chat is sent to Google Translate** to be translated (MyMemory if
  Google is unavailable). In-match chat is public; turn translation off in
  Settings if you'd rather it wasn't sent anywhere. What you type into the
  reply helper goes the same way.
- **On start, the app asks GitHub** whether a newer version is out. Nothing
  is downloaded; turn it off in Settings.
- **Only Windows' audio APIs** (the same ones OBS and Discord use) and the log
  CS2 writes with Valve's own `-condebug` option. No memory reads, no
  injection, no input sent to the game, no driver.

Use at your own risk: no third-party tool can be guaranteed against future
anti-cheat changes.

</details>

## Troubleshooting

<details>
<summary>No text chat</summary>

<br>

`-condebug` is missing from CS2's launch options (the app reminds you on
start), or CS2 is in a Steam library the app didn't find. In that case set
`chat.console_log_path` as described in the
[configuration guide](docs/CONFIGURATION.md).

</details>

<details>
<summary>The overlay doesn't show in game</summary>

<br>

Switch CS2 to **Fullscreen Windowed**. Exclusive fullscreen draws over every
other window, including the overlay.

</details>

<details>
<summary>Game sounds or music become captions</summary>

<br>

Move **Speech detection** toward *Stricter* in Settings.

</details>

<details>
<summary>Short callouts are missing</summary>

<br>

Lower **Ignore speech shorter than** in Settings, and check the speaker's
language is ticked under **Translate speech in**.

</details>

<details>
<summary>The first launch is slow</summary>

<br>

It's downloading the speech model and preparing it for your hardware. That
only happens once.

</details>

<details>
<summary>Something else</summary>

<br>

The log at `%LOCALAPPDATA%\CSVoiceCaptions\cs-voice-captions.log` says what
was heard, what was captioned, and why anything else was dropped.
[How it works](docs/HOW-IT-WORKS.md) explains what it means.

</details>

## More

- [Configuration](docs/CONFIGURATION.md): every setting, and the slang
  dictionary.
- [How it works](docs/HOW-IT-WORKS.md): how speech is detected, filtered and
  translated, with measurements.
- [Contributing](CONTRIBUTING.md): running from source, building, releasing.

## Licence

MIT. See [LICENSE](LICENSE).
