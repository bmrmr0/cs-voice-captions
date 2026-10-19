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

## What gets filtered, and why

A clip has to pass every one of these before it becomes a caption:

1. **It is speech**, per the voice detector (`vad.speech_threshold`).
2. **It is long enough** (`vad.min_utterance_s`, half a second by default).
   Most callouts are under two seconds, so this is kept short.
3. **It is in a language you picked** (`translation.languages`). On short
   clips Whisper's language guess is close to random: one evening's junk clips
   came back as Korean, Chinese, Tamil, Arabic, German, Portuguese and Turkish.
   Naming the languages you want removes most of it.
4. **A second opinion agrees on the language** (`translation.verify_language`).
   The NPU build of the model is *consistently* wrong about short clips: it
   called English speech, CS2's own voice lines and plain noise "Russian", and
   asking it again with the clip shifted gave the same wrong answer. A second
   copy on the CPU makes different mistakes. Of every clip checked by hand, all
   genuinely Russian ones were Russian on both, and every false one disagreed.
5. **It isn't English**, unless you've turned English on.
6. **It isn't a stock phrase**: Whisper's habitual inventions ("Thanks for
   watching!", subtitle credits) and CS2's scripted radio lines ("Throwing
   smoke!") are dropped by exact match. Generic lines like "go go go" are kept,
   because real teammates say them too.
7. **It isn't a repeat** of one of the last 20 captions (`vad.repeat_window`),
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

The log is at `%LOCALAPPDATA%\CSVoiceCaptions\cs-voice-captions.log` and is
rewritten on every start. Every filter says why it dropped something, with the
setting and the value it wanted:

```
[audio:teammates] clip dropped: phrase ran 0.3s (vad.min_utterance_s wants 0.5s)
[stt] skipped, detected pl (translation.languages): Nice.
[stt] skipped, detected ru but the CPU check heard en (translation.verify_language): Down here.
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
