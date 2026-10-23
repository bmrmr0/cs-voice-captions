"""Put the app's speech workload on the machine on a schedule, so its cost
to the game can be measured when nobody happens to be talking.

    .venv\\Scripts\\python tools\\speech_load.py

Runs saved voice clips (the app's clips/ folder) through the same
processing the app gives every clip -- language, translation, slang --
once every few seconds, in phases:

    quiet  -> now (this build: NPU, efficiency cores, below-normal priority)
    quiet  -> old (as builds before 22 Oct evening: language on the CPU,
                   normal priority, all cores)
    quiet

Each phase's start goes to perf/phases-<time>.csv, so a recording made
with perf_record.py at the same time can be split by phase.
"""
import argparse
import csv
import glob
import io
import contextlib
import os
import random
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import psutil  # noqa: E402

import config as config_mod  # noqa: E402
import cpu as cpu_mod  # noqa: E402


def load_clips():
    out = []
    for c in sorted(glob.glob(os.path.join(config_mod.data_dir(), "clips", "*kept*.wav"))):
        with wave.open(c) as w:
            out.append(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
                       .astype(np.float32) / 32768.0)
    return out


def make_transcriber(old):
    import stt
    cfg = config_mod.load()
    if old:
        # The old way: no NPU language pass, so the CPU copy is used, with
        # four threads on any core at normal priority.
        import lang_id

        def refuse(*_a, **_k):
            raise RuntimeError("old behaviour")
        lang_id.LanguageId = refuse
        cpu_mod.detector_threads = lambda: 4
    with contextlib.redirect_stdout(io.StringIO()):
        t = stt.Transcriber(cfg)
    return t


def run_phase(name, seconds, every, clips, t, log):
    log.writerow([time.strftime("%H:%M:%S"), name])
    print(f"[load] {time.strftime('%H:%M:%S')} {name} for {seconds:.0f} s", flush=True)
    end = time.time() + seconds
    while time.time() < end:
        start = time.time()
        if t is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                t.process("teammates", random.choice(clips))
            t._recent.clear()
            t._last_text = ""
        time.sleep(max(0.0, every - (time.time() - start)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=4.0, help="seconds between clips")
    ap.add_argument("--phase", type=float, default=120.0, help="seconds per busy phase")
    ap.add_argument("--quiet", type=float, default=60.0, help="seconds per quiet phase")
    args = ap.parse_args()
    clips = load_clips()
    if not clips:
        sys.exit("no saved clips to play")
    out = os.path.join(config_mod.data_dir(), "perf", time.strftime("phases-%Y%m%d-%H%M%S.csv"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    me = psutil.Process()
    with open(out, "w", newline="", encoding="utf-8") as fh:
        log = csv.writer(fh)
        log.writerow(["time", "phase"])

        # This build's settings, as main.py sets them.
        me.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        cores = cpu_mod.efficiency_cores()
        if len(cores) >= 4:
            me.cpu_affinity(cores)
        now = make_transcriber(old=False)
        run_phase("quiet", args.quiet, args.every, clips, None, log)
        fh.flush()
        run_phase("now", args.phase, args.every, clips, now, log)
        fh.flush()
        del now

        me.nice(psutil.NORMAL_PRIORITY_CLASS)
        me.cpu_affinity(list(range(psutil.cpu_count())))
        old = make_transcriber(old=True)
        run_phase("quiet", args.quiet, args.every, clips, None, log)
        fh.flush()
        run_phase("old", args.phase, args.every, clips, old, log)
        fh.flush()
        run_phase("quiet", args.quiet, args.every, clips, None, log)
        log.writerow([time.strftime("%H:%M:%S"), "end"])
    print(f"[load] done; phases in {out}")


if __name__ == "__main__":
    main()
