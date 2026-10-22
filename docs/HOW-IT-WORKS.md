# How it works

## The two pipelines

| | Source | How |
| --- | --- | --- |
| **Voice** | CS2's audio | Per-process WASAPI loopback captures `cs2.exe` alone, so Discord and music never reach the captions. A neural voice detector picks speech out of the game sound, and Whisper transcribes and translates it. |
| **Text chat** | `console.log` | CS2 writes this itself when started with `-condebug`. The app follows the file as it grows and translates what appears. |

Neither reads the game's memory, injects into it or sends it input.

## Telling a teammate apart from a gunfight

The app hears *all* of CS2's audio, so the hard part isn't transcription.
It's deciding what counts as someone talking. A conventional voice detector
marks gunfire, footsteps and music as speech more or less continuously, and
Whisper will confidently invent a sentence for every one of them. In an early
version, 70 of 76 clips ran to the maximum length because the detector never
heard a gap, and each became a made-up caption.

Silero, a small neural detector, separates the two cleanly. Measured on this
pipeline:

| Input | Peak score | Frames called speech |
| --- | --- | --- |
| Speech | **1.000** | 79% |
| Gunfire-like bursts | 0.062 | 0% |
| White noise, loud | 0.035 | 0% |
| Pure tone | 0.002 | 0% |
| Silence | 0.009 | 0% |

It runs on the CPU in about a millisecond per frame.

## Captions that don't cost you frames

Whisper runs through OpenVINO, so on a machine with an Intel NPU ("AI Boost",
Core Ultra and newer) transcription happens on the NPU and never touches the
GPU that's running the game. On a Core Ultra 9 it's also faster than the CPU:

| Device | 10 seconds of audio |
| --- | --- |
| NPU | **0.40 s** |
| CPU | 2.19 s |

## Which language? Asked of the CPU

On the NPU, Whisper's guess at a clip's language depends on the clips it
heard before. The same 30 clips run forwards and then backwards came back
in different languages for 25 of them (OpenVINO 2026.3 and 2026.4 alike),
while the CPU build gave the same answer every time, as did a freshly
loaded NPU model on its first clip. That is why the NPU seemed to hear
Russian in English, CS2's radio and noise, and as often missed real Russian.

So a copy of the model on the CPU decides the language of each clip (one
encoder pass, about 0.95 s, ~370 MB), and the NPU then transcribes and
translates it told which language it is. Told the language, the NPU's output
no longer depends on what came before. On 121 saved clips the old approach
(NPU guesses, CPU confirms) captioned 4 of the 21 Russian ones; this one
captions all 21, and gives the same captions in either order.

A clip in a language you don't caption is dropped after the CPU pass and
never reaches the NPU.

## What gets filtered, and why

A clip has to pass every one of these before it becomes a caption:

1. **It is speech**, per the voice detector (`vad.speech_threshold`).
2. **It is long enough** (`vad.min_utterance_s`, 0.7 s by default). On 157
   saved clips, of the captions in the picked languages under 0.7 s of
   speech, 1 of 6 was real; above it, real callouts take over.
   Most callouts are under two seconds, so this is kept short.
3. **It is in a language you picked** (`translation.languages`). On short
   clips the guess is shaky even on the CPU: noise and mumbling come back
   as Dutch, Romanian or Japanese. Naming the languages you want removes
   most of it.
4. **It isn't English**, unless you've turned English on.
5. **It isn't a stock phrase**: Whisper's habitual inventions ("Thanks for
   watching!", subtitle credits) and CS2's scripted radio lines ("Throwing
   smoke!") are dropped by exact match. Generic lines like "go go go" are kept,
   because real teammates say them too.
6. **It isn't a repeat** of one of the last 20 captions (`vad.repeat_window`),
   which is what music kits do every round.

Phrases end after 0.9 s of silence (`vad.silence_ms`). With 0.5 s, ordinary
pauses split one sentence into fragments too short to translate: on one
8.4-second sentence, 0.5 s gave a 2.5 s fragment that was dropped plus a 5 s
clip, while 0.9 s gave one 8 s clip and dropped nothing.

## Slang and translation

Whisper translates speech straight to English, and its English is often
right in words but wrong in meaning for gaming slang. The
[slang dictionary](CONFIGURATION.md#slang-dictionary) fixes that in three
ways: whole phrases are replaced (`есть!` → "Got him!"), insults get a note
under the caption, and for chat, slang with a clean English equivalent is
swapped in *before* translation, so the translator never sees it.

Chat goes to Google Translate through two of its public endpoints, each with
its own back-off when throttled, and to MyMemory only when both refuse. Its
translations are much worse: `выблядок` came back as "pancakes".

## Reading the log

The log is at `%LOCALAPPDATA%\CSVoiceCaptions\cs-voice-captions.log` (tray
menu → Troubleshooting → Open log). Each start moves the last one to
`cs-voice-captions.previous.log`. Every filter says why it dropped something, with the
setting and the value it wanted:

```
[audio:teammates] clip dropped: phrase ran 0.3s (vad.min_utterance_s wants 0.7s)
[stt] skipped, detected pl (translation.languages)
[stt] skipped, detected en (translation.only_foreign)
```

A minute with nothing captioned reports what was heard, which tells a quiet
game from a detector that's too strict from audio that never arrived:

```
[audio:teammates] nothing captioned in the last minute — loudest level 0.412, best speech score 0.06 (needs 0.5)
```

A high level with a low speech score means sound is arriving but none of it
sounds like a person talking. A level of `0.000` means CS2 produced no sound
at all (it is in the menu, or muted while in the background).

## Why voices aren't labelled with player names

CS2 mixes every teammate into one audio stream, and the only way to know who
is talking would be to read it from the game, which is exactly what this app
doesn't do. Running from source, an optional extra (`requirements-diarization.txt`)
can tell distinct voices apart and label them P1, P2, … It isn't in the exe.
