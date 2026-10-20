# Configuration

Most people never need this: the **Settings** window (tray icon → Settings…)
covers everything that's safe to change while you play, and explains each
option when you hover over it. This page is for everything else.

## Where settings live

The exe has every default built in, so it needs no config file. To change
something the Settings window doesn't offer, create `config.json` in
`%LOCALAPPDATA%\CSVoiceCaptions` (beside the exe in portable mode, or the
repository folder when running from source) containing **only the keys you
want to change**:

```json
{
  "stt": { "model": "medium" },
  "hotkeys": { "toggle_pause": "ctrl+shift+f9" }
}
```

The Settings window and the overlay write to the same file, and only ever add
the values you have changed, so a future version's improved defaults still
reach you. Restart the app after editing it by hand.

## Settings

*Settings* in the last column means the option is also in the Settings
window.

### Captions and translation

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `translation.languages` | `["ru", "uk"]` | Spoken languages to caption, as two-letter codes. `[]` means any language, which lets through more junk: on short clips the language guess is unreliable. | Settings |
| `translation.only_foreign` | `true` | Hide English speech, since you can hear it yourself. | Settings |
| `translation.show_original` | `false` | Show the original text, in grey, beside each translation. | Settings |
| `translation.slang` | `true` | Explain slang and swear words from the [slang dictionary](#slang-dictionary). | Settings |
| `translation.target_language` | `"English"` | Language text chat is translated into. Voice captions are always English: that's a limit of the speech model. | |
| `translation.translate_sources` | `["teammates"]` | Which audio sources get translated rather than transcribed. | |

### Text chat

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `chat.show` | `true` | Show chat lines. Also in the tray menu. | Settings |
| `chat.translate` | `true` | Translate chat that isn't in your language. | Settings |
| `chat.translator` | `"google"` | `"google"` (with MyMemory as a fallback), or `"off"` to never send chat anywhere. | |
| `chat.scopes` | `["ALL", "TEAM"]` | Which chat to show. | |
| `chat.console_log_path` | `""` | Full path to CS2's `console.log`, if the app can't find your Steam library. It's normally `…\steamapps\common\Counter-Strike Global Offensive\game\csgo\console.log`. | |
| `chat.remind_condebug` | `true` | Remind on start when CS2's launch options lack `-condebug`. "Don't remind me again" sets it to `false`. | |
| `chat.enabled` | `true` | Read text chat at all. | |
| `chat.muted` | `[]` | Players whose chat is hidden, by name. Right-click a name in the captions window to add one. | Settings (unmute) |
| `reply.language` | `"ru"` | The language the reply helper translates into. Remembered from its window. | |

### Voice detection

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `vad.min_utterance_s` | `0.5` | Ignore speech shorter than this many seconds. | Settings |
| `vad.speech_threshold` | `0.5` | How sure the detector must be that someone is talking (0–1). Higher ignores more game sound and music; lower catches quieter speech. | Settings |
| `vad.silence_ms` | `900` | Silence that ends a phrase. Too low splits sentences into fragments too short to translate well. | |
| `vad.max_utterance_s` | `8` | Longest single caption; longer speech is split. | |
| `vad.repeat_window` | `20` | Hide a caption identical to one of the last N. Catches music kits, which sing the same line every round. | |
| `vad.blocklist` | `[]` | Regular expressions for captions to drop outright, for example `["let the bodies hit the floor"]`. | |
| `vad.min_speech_ms` | `200` | Drop clips with less actual speech than this. | |
| `vad.min_speech_ratio` | `0.25` | Drop clips where less than this share of the phrase is speech. | |

### Audio capture

| Key | Default | What it does |
| --- | --- | --- |
| `sources.teammates` | `true` | Caption CS2's audio. |
| `sources.my_mic` | `false` | Also caption your own microphone. |
| `capture.teammates_backend` | `"process"` | `"process"` captures CS2 only; `"loopback"` captures the whole desktop. |
| `capture.fallback_to_loopback` | `true` | If CS2-only capture can't start, capture the whole desktop instead of nothing. |
| `capture.cs2_process_name` | `"cs2.exe"` | The process to capture. |

### Speech model

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `stt.model` | `"small"` | `tiny`, `base`, `small`, `medium`. Bigger is more accurate and slower, and downloads once. `medium` is **Translation quality: Better** in Settings: on 21 Russian test clips it translated 8 better and 4 worse than `small`, at about 0.85 s a clip instead of 0.3 s on the NPU, after a 1.5 GB download. (`large-v3-turbo` is faster but translates poorly.) Applies at the next start. | Settings |
| `stt.device` | `"auto"` | NPU if there is one, else the CPU. Force it with `"NPU"`, `"CPU"` or `"GPU"`. The GPU is never chosen automatically, because it's busy running the game. | |
| `stt.beam_size` | `1` | Raise to `5` for slightly better accuracy at the cost of speed. | |

### Overlay, window and hotkeys

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `overlay.font_size` | `20` | Text size, in pixels. | Settings |
| `overlay.width` | `720` | Width, in pixels. | Settings |
| `overlay.max_lines` | `5` | Captions shown at once. | Settings |
| `overlay.line_ttl_s` | `12` | Seconds each caption stays. | Settings |
| `overlay.enabled` | `false` | Show the overlay on start. Remembered from the tray menu. | |
| `overlay.position` | `"custom"` | `"custom"` (where you dragged it), or `top-left`, `top-center`, `top-right`, `bottom-left`, `bottom-center`, `bottom-right` on the main screen. At the bottom it grows upwards. | Settings |
| `overlay.x`, `overlay.y` | `40` | Position. Set by dragging the unlocked overlay. | |
| `history.max_lines` | `500` | Lines kept in the captions window. | |
| `hotkeys.toggle_pause` | `"ctrl+alt+p"` | Pause / resume. | |
| `hotkeys.toggle_overlay` | `"ctrl+alt+o"` | Show / hide the overlay. | |
| `hotkeys.show_window` | `"ctrl+alt+c"` | Bring up the captions window. | |
| `hotkeys.reply` | `"ctrl+alt+r"` | Open the reply helper. | |
| `hotkeys.enabled` | `true` | Register hotkeys at all. | |

A hotkey needs at least one modifier (`ctrl`, `alt`, `shift`, `win`) plus a
key, for example `"ctrl+shift+f9"`. If another program already owns a
combination, the app says so in the log and carries on without it.

### Transcripts

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `transcript.enabled` | `false` | Write every caption to a text file you can read after the match, one file per session. | Settings |
| `transcript.directory` | `""` | Where to write it; blank means a `transcripts` folder in the app's data folder. | |

### Updates and startup

| Key | Default | What it does | |
| --- | --- | --- | --- |
| `updates.check` | `true` | On start, ask GitHub whether a newer release is out and say so, with a link. Nothing is downloaded or installed. | Settings |

**Start with Windows** is in Settings too. It isn't kept in `config.json`
but as an entry under the current user's
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` key, the usual place
for per-user startup programs; turning it off removes the entry. If you move
the exe, the entry follows it the next time you run it.

### For debugging

| Key | Default | What it does |
| --- | --- | --- |
| `vad.save_clips` | `false` | Save every detected clip as a `.wav` in `clips/`, named after the rule that dropped it, to see what a filter is throwing away. Capped at 400 files. They are recordings of other players, so delete them when you're done. |
| `vad.backend` | `"silero"` | The voice detector. `"webrtc"` is an older one that can't tell gunfire from speech; only for comparison. |
| `vad.aggressiveness` | `2` | Only used by the `"webrtc"` detector. |

## Slang dictionary

Machine translation renders gaming slang word for word and gets it wrong:
`есть!` is "Got him!", not "Yes"; `петух` is a severe insult, not "rooster".
A dictionary, `slang.json`, fixes what it can. It's built into the exe, and
you can add your own words from **Settings → Edit dictionary…**, which opens
your own `slang.json` in the app's data folder. Yours is merged over the
built-in one, and saved changes are picked up within a few seconds.

Each language code has up to three sections:

```json
{
  "ru": {
    "phrases": { "есть": "Got him!" },
    "terms":   { "петух*": "severe insult (≈ 'bitch')" },
    "replace": { "вх": "wallhack" }
  },
  "any": {
    "terms": { "cyka": "bitch (Russian 'сука')" }
  }
}
```

- **phrases** replace the whole caption when the message is nothing but that
  phrase. Repeats are allowed: `Есть, есть, есть!` matches `есть`.
- **terms** add a short note under the caption wherever the word appears. The
  translation itself is left alone, since swapping one word inside a
  machine translation would garble the rest.
- **replace** swaps a word for its English *before* a chat line is sent to
  the translator, so the translation comes out right: `легко с вх играть`
  becomes "easy to play with wallhack" instead of "…with input". Use it only
  for words with a clean one-word English equivalent.

A trailing `*` matches any ending, so `петух*` also catches `петуха`. Mind
Russian's dropped vowels: `выблядок` becomes `выблядки`, so the stem must be
`выбляд*`, not `выблядк*`. Matching ignores case, punctuation and, for
Latin-script languages, accents. The `any` section is checked for every
message, which suits transliterated swearing like `cyka blyat` or `kurwa`.
