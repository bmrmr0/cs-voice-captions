"""Show the app's own overlay window with made-up captions on a schedule,
to measure what drawing it over the game costs.

    .venv\\Scripts\\python tools\\overlay_load.py

Phases: hidden 60 s, shown with a caption every 3 s for 120 s, hidden
60 s, shown 120 s, hidden 60 s. Each phase's start goes to
perf/phases-<time>.csv, for perf_record.py's recording made at the same
time. The captions are invented text, nothing from a match.
"""
import csv
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config as config_mod  # noqa: E402

LINES = ["Rotate B, they're all in the tunnel", "One left, he's low", "Going long, cover me",
         "Smoke mid in three", "Nice shot!", "Save, we can't win this one"]
PLAN = [("hidden", 60), ("shown", 120), ("hidden", 60), ("shown", 120), ("hidden", 60)]


def main():
    app = QApplication(sys.argv)
    from ui import Overlay
    cfg = config_mod.load()
    ocfg = dict(cfg["overlay"])
    ocfg["x"], ocfg["y"] = ocfg.get("x", 40), ocfg.get("y", 40) + 260   # beside the real one
    ov = Overlay(ocfg, process_name=cfg["capture"].get("cs2_process_name", "cs2.exe"))
    out = os.path.join(config_mod.data_dir(), "perf", time.strftime("phases-%Y%m%d-%H%M%S.csv"))
    fh = open(out, "w", newline="", encoding="utf-8")
    log = csv.writer(fh)
    log.writerow(["time", "phase"])
    state = {"i": -1, "n": 0}

    def next_phase():
        state["i"] += 1
        if state["i"] >= len(PLAN):
            log.writerow([time.strftime("%H:%M:%S"), "end"])
            fh.close()
            print(f"[overlay] done; phases in {out}")
            app.quit()
            return
        name, secs = PLAN[state["i"]]
        log.writerow([time.strftime("%H:%M:%S"), "overlay " + name])
        fh.flush()
        print(f"[overlay] {time.strftime('%H:%M:%S')} {name} for {secs} s", flush=True)
        (ov.show if name == "shown" else ov.hide)()
        QTimer.singleShot(secs * 1000, next_phase)

    def caption():
        if ov.isVisible():
            state["n"] += 1
            ov.add_caption({"label": "Teammate", "text": LINES[state["n"] % len(LINES)],
                            "color": "#7CFC7C", "lang": "ru", "translated": True,
                            "ts": time.strftime("%H:%M:%S")})

    ticker = QTimer()
    ticker.timeout.connect(caption)
    ticker.start(3000)
    next_phase()
    app.exec()


if __name__ == "__main__":
    main()
