"""Record, once a second, what the app is doing next to how CS2 is running,
to find out whether (and when) the app costs the game frames.

    .venv\\Scripts\\python tools\\perf_record.py
    .venv\\Scripts\\python tools\\perf_record.py --presentmon C:\\path\\PresentMon.exe

Start it while CS2 and the app are running, play, then press Ctrl+C. It
writes a CSV (one row a second) and prints a summary comparing the seconds
with the overlay on or off, and with or without the app working on speech.

Columns:
  app:   running, CPU (% of one core), priority, cores it may use, memory,
         overlay shown and its height, captions window shown/minimized, and
         from its log: clips dropped, clips transcribed, captions, chat lines
  cs2:   CPU (% of one core), memory, GPU use of its own (Windows counters)
  cpu:   whole machine, performance cores, efficiency cores (average %)
  gpu:   NVIDIA usage, clock, power, temperature (nvidia-smi), and the
         app's own GPU/NPU use by engine type (Windows counters)
  fps:   only with PresentMon (Intel's open-source frame tracer, which needs
         an administrator prompt): frames, average FPS, worst frame time

Everything stays on this PC. The CSV goes to the app's data folder, under
perf/.
"""
import argparse
import csv
import ctypes
import ctypes.wintypes as W
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import psutil  # noqa: E402

import config as config_mod  # noqa: E402
import cpu as cpu_mod  # noqa: E402

APP = "csvoicecaptions.exe"
GAME = "cs2.exe"


# -- background readers -----------------------------------------------------
class Streamer(threading.Thread):
    """Runs a command that prints a line now and then, keeps the latest."""

    def __init__(self, cmd, parse):
        super().__init__(daemon=True)
        self.cmd, self.parse, self.latest, self.proc = cmd, parse, {}, None

    def run(self):
        while True:
            try:
                self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL, text=True,
                                             creationflags=0x08000000)    # no window
                for line in self.proc.stdout:
                    got = self.parse(line)
                    if got:
                        self.latest = got
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)


def nvidia_line(line):
    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 4:
        return None
    try:
        util, clock, power, temp = (float(p) for p in parts)
    except ValueError:
        return None
    return {"nv_gpu_util": util, "nv_clock_mhz": clock, "nv_power_w": power, "nv_temp_c": temp}


class GpuEngines(threading.Thread):
    """Windows' per-process GPU engine counters, through typeperf. Restarted
    every two minutes, since typeperf only sees the processes that existed
    when it started."""

    def __init__(self):
        super().__init__(daemon=True)
        self.latest = {}

    def run(self):
        while True:
            try:
                p = subprocess.Popen(["typeperf", r"\GPU Engine(*)\Utilization Percentage",
                                      "-si", "1", "-sc", "120"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, creationflags=0x08000000)
                header = None
                for line in p.stdout:
                    row = next(csv.reader([line]), [])
                    if len(row) < 2:
                        continue
                    if header is None:
                        header = row
                        continue
                    sums = {}
                    for name, val in zip(header[1:], row[1:]):
                        m = re.search(r"pid_(\d+)_.*engtype_([A-Za-z0-9 ]+)\)", name)
                        if not m:
                            continue
                        try:
                            v = float(val)
                        except ValueError:
                            continue
                        key = (int(m.group(1)), m.group(2).strip())
                        sums[key] = sums.get(key, 0.0) + v
                    self.latest = sums
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)


class PresentMon(threading.Thread):
    """Frames CS2 presented in each second, from PresentMon's CSV stream."""

    def __init__(self, exe):
        super().__init__(daemon=True)
        self.exe, self.lock, self.frames = exe, threading.Lock(), []

    def run(self):
        cmd = [self.exe, "--process_name", GAME, "--output_stdout",
               "--stop_existing_session", "--session_name", "cvc_perf_record"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        col = None
        for line in p.stdout:
            row = next(csv.reader([line]), [])
            if col is None:
                for name in ("MsBetweenPresents", "FrameTime", "msBetweenPresents"):
                    if name in row:
                        col = row.index(name)
                        break
                if col is None and len(row) < 5:
                    print("[presentmon]", line.strip())
                continue
            try:
                ms = float(row[col])
            except (ValueError, IndexError):
                continue
            with self.lock:
                self.frames.append(ms)

    def take(self):
        with self.lock:
            f, self.frames = self.frames, []
        if not f:
            return {"frames": 0, "fps": "", "worst_frame_ms": ""}
        return {"frames": len(f), "fps": round(1000.0 * len(f) / max(sum(f), 1e-6), 1),
                "worst_frame_ms": round(max(f), 1)}


# -- app state --------------------------------------------------------------
_user32 = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


def app_windows(pids):
    """Overlay: the app's always-on-top, layered window. Captions window:
    its ordinary one. Returns their state."""
    out = {"overlay_shown": 0, "overlay_height": 0, "captions_shown": 0, "captions_minimized": 0}

    def visit(hwnd, _):
        pid = W.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        buf = ctypes.create_unicode_buffer(64)
        _user32.GetWindowTextW(hwnd, buf, 64)
        if buf.value != "CS Voice Captions":
            return True
        ex = _user32.GetWindowLongW(hwnd, -20)
        visible = bool(_user32.IsWindowVisible(hwnd))
        if ex & 0x8 and ex & 0x80000:                # topmost + layered: overlay
            r = W.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(r))
            out["overlay_shown"] = int(visible)
            out["overlay_height"] = (r.bottom - r.top) if visible else 0
        elif visible:
            out["captions_shown"] = 1
            out["captions_minimized"] = int(bool(_user32.IsIconic(hwnd)))
        return True

    _user32.EnumWindows(_EnumProc(visit), 0)
    return out


class LogTail:
    """Counts what the app's log says happened since the last call."""

    PATTERNS = {"clips_dropped": "clip dropped", "clips_transcribed": "[stt] detected",
                "skipped_language": "(translation.", "captions": "[caption:",
                "chat_lines": "[chat:"}

    def __init__(self, path):
        self.path, self.pos = path, None

    def take(self):
        counts = dict.fromkeys(self.PATTERNS, 0)
        try:
            size = os.path.getsize(self.path)
            if self.pos is None or size < self.pos:
                self.pos = size                     # start from now / log restarted
                return counts
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.pos)
                text = f.read()
                self.pos = f.tell()
        except OSError:
            return counts
        for line in text.splitlines():
            for key, needle in self.PATTERNS.items():
                if needle in line:
                    counts[key] += 1
        return counts


# -- main loop --------------------------------------------------------------
def procs(name):
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == name]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--presentmon", help="path to PresentMon.exe, for FPS (needs admin)")
    ap.add_argument("--minutes", type=float, default=0, help="stop after this long (0 = Ctrl+C)")
    args = ap.parse_args()

    out_dir = os.path.join(config_mod.data_dir(), "perf")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, time.strftime("perf-%Y%m%d-%H%M%S.csv"))
    ecores = set(cpu_mod.efficiency_cores())
    ncpu = psutil.cpu_count()
    pcores = set(range(ncpu)) - ecores if ecores else set()

    nv = Streamer(["nvidia-smi", "--query-gpu=utilization.gpu,clocks.gr,power.draw,temperature.gpu",
                   "--format=csv,noheader,nounits", "-lms", "1000"], nvidia_line)
    nv.start()
    engines = GpuEngines()
    engines.start()
    pm = None
    if args.presentmon:
        pm = PresentMon(args.presentmon)
        pm.start()
    tail = LogTail(os.path.join(config_mod.data_dir(), "cs-voice-captions.log"))
    tail.take()

    tracked = {}
    psutil.cpu_percent(percpu=True)
    rows, fields = [], None
    start = time.time()
    print(f"[perf] recording to {out_path}  (Ctrl+C to stop)")
    try:
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = None
            while not args.minutes or time.time() - start < args.minutes * 60:
                time.sleep(1.0 - (time.time() - start) % 1.0)
                per = psutil.cpu_percent(percpu=True)
                row = {"time": time.strftime("%H:%M:%S"),
                       "cpu_total": round(sum(per) / len(per), 1),
                       "cpu_pcores": round(sum(per[i] for i in pcores) / len(pcores), 1) if pcores else "",
                       "cpu_ecores": round(sum(per[i] for i in ecores) / len(ecores), 1) if ecores else "",
                       "ram_used_pct": psutil.virtual_memory().percent}
                app = procs(APP)
                game = procs(GAME)
                for p in app + game:
                    if p.pid not in tracked:
                        tracked[p.pid] = p
                        p.cpu_percent(None)
                main_app = max(app, key=lambda p: p.memory_info().rss) if app else None
                row["app_running"] = int(bool(main_app))
                if main_app:
                    try:
                        row["app_cpu"] = round(main_app.cpu_percent(None), 1)
                        row["app_priority"] = str(main_app.nice()).split(".")[-1].replace("_PRIORITY_CLASS", "")
                        row["app_cores"] = len(main_app.cpu_affinity())
                        row["app_mem_mb"] = main_app.memory_info().rss // 2**20
                    except psutil.Error:
                        pass
                row.update(app_windows({p.pid for p in app}))
                row.update(tail.take())
                g = game[0] if game else None
                row["cs2_running"] = int(bool(g))
                if g:
                    try:
                        row["cs2_cpu"] = round(g.cpu_percent(None), 1)
                        row["cs2_mem_mb"] = g.memory_info().rss // 2**20
                    except psutil.Error:
                        pass
                eng = engines.latest
                if g:
                    row["cs2_gpu_3d"] = round(sum(v for (pid, t), v in eng.items()
                                                  if pid == g.pid and t == "3D"), 1)
                if main_app:
                    mine = {}
                    for (pid, t), v in eng.items():
                        if pid == main_app.pid:
                            mine[t] = mine.get(t, 0.0) + v
                    row["app_gpu_3d"] = round(mine.get("3D", 0.0), 1)
                    row["app_compute"] = round(sum(v for t, v in mine.items() if t.startswith("Compute")), 1)
                row.update(nv.latest)
                if pm:
                    row.update(pm.take())
                if writer is None:
                    fields = list(row) + [k for k in ("app_cpu", "app_priority", "app_cores", "app_mem_mb",
                                                      "cs2_cpu", "cs2_mem_mb", "cs2_gpu_3d", "app_gpu_3d",
                                                      "app_compute", "nv_gpu_util", "nv_clock_mhz",
                                                      "nv_power_w", "nv_temp_c", "frames", "fps",
                                                      "worst_frame_ms") if k not in row]
                    writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                writer.writerow(row)
                fh.flush()
                rows.append(row)
                if len(rows) % 10 == 0:
                    print(f"[perf] {row['time']}  cs2 cpu {row.get('cs2_cpu', '-')}%  app cpu "
                          f"{row.get('app_cpu', '-')}%  overlay {'on' if row['overlay_shown'] else 'off'}  "
                          f"gpu {row.get('nv_gpu_util', '-')}%  fps {row.get('fps', 'n/a')}")
    except KeyboardInterrupt:
        pass
    summarize(rows, out_path)


def _avg(rows, key):
    vals = [float(r[key]) for r in rows if r.get(key) not in (None, "")]
    return round(sum(vals) / len(vals), 1) if vals else "-"


def summarize(rows, path):
    rows = [r for r in rows if r.get("cs2_running")]
    print(f"\n[perf] {len(rows)} seconds with CS2 running. CSV: {path}")
    if not rows:
        return
    busy = [r for r in rows if r.get("clips_transcribed") or r.get("captions")]
    groups = {
        "all": rows,
        "overlay on": [r for r in rows if r.get("overlay_shown")],
        "overlay off": [r for r in rows if not r.get("overlay_shown")],
        "app working on speech": busy,
        "app quiet": [r for r in rows if r not in busy and r.get("app_running")],
        "app not running": [r for r in rows if not r.get("app_running")],
    }
    keys = ["fps", "worst_frame_ms", "cs2_cpu", "app_cpu", "cpu_pcores", "cpu_ecores", "nv_gpu_util"]
    print(f"{'':24}{'secs':>6}" + "".join(f"{k:>16}" for k in keys))
    for name, g in groups.items():
        if g:
            print(f"{name:24}{len(g):>6}" + "".join(f"{_avg(g, k):>16}" for k in keys))


if __name__ == "__main__":
    main()
