"""Build dist/CSVoiceCaptions.exe.

    .venv\\Scripts\\pip install -r requirements.txt -r requirements-build.txt
    .venv\\Scripts\\python build.py

Produces one self-contained, no-console-window executable. Nothing ships
beside it: every default is compiled in, and the speech model, settings,
compiled device cache and transcripts all live in
%LOCALAPPDATA%\CSVoiceCaptions, downloaded once on first run.

The build is deliberately reproducible and identity-free: no absolute paths
from this machine end up in the binary beyond what PyInstaller needs, and
`--clean` wipes any earlier build tree so a previous machine's paths cannot
survive into a published release.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "CSVoiceCaptions"
ENTRY = os.path.join(ROOT, "main.py")

# Imported lazily or only through config strings, so PyInstaller's static
# analysis cannot see them on its own.
HIDDEN_IMPORTS = [
    "webrtcvad",
    "soundcard",
    "proctap",
    "psutil",
    "deep_translator",
    "requests",
    "scipy.signal",
    "openvino",
    "openvino_genai",
    "huggingface_hub",
]

# OpenVINO loads its core, its CPU/GPU/NPU plugins and the tokenizers
# extension as loose DLLs by name at runtime. PyInstaller's static analysis
# cannot see any of that, so collect these packages wholesale.
COLLECT_ALL = ["openvino", "openvino_genai", "openvino_tokenizers"]

# Nothing here is used at runtime; excluding it keeps the exe from doubling in
# size because something in the venv happens to import it.
EXCLUDES = [
    "tkinter", "matplotlib", "IPython", "notebook", "pytest",
    "torch", "torchaudio", "torchvision", "resemblyzer",
    "faster_whisper", "ctranslate2", "av",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
]


def silero_model():
    """The Silero VAD model gets bundled (about 2.7 MB) rather than downloaded,
    so voice detection works on the very first launch, before anything has
    been fetched."""
    try:
        import silero_vad
    except ImportError:
        return None
    path = os.path.join(os.path.dirname(silero_vad.__file__), "data",
                        "silero_vad_op18_ifless.onnx")
    return path if os.path.isfile(path) else None


def clean():
    """Remove previous build output. Also the point at which any stale tree
    from another machine (with its paths baked into the .toc files) is gone."""
    for d in ("build", "dist"):
        path = os.path.join(ROOT, d)
        if os.path.isdir(path):
            print(f"[build] removing {d}/")
            shutil.rmtree(path, ignore_errors=True)
    spec = os.path.join(ROOT, f"{NAME}.spec")
    if os.path.isfile(spec):
        os.remove(spec)


def main():
    ap = argparse.ArgumentParser(description="Build the CS Voice Captions exe.")
    ap.add_argument("--console", action="store_true",
                    help="keep a console window open for debugging")
    ap.add_argument("--no-clean", action="store_true",
                    help="reuse the previous build tree (faster, less safe)")
    args = ap.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing. Run:\n"
                 "  pip install -r requirements-build.txt")

    if not args.no_clean:
        clean()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--name", NAME,
        "--console" if args.console else "--windowed",
        # Keep the working directory out of the module search path so the exe
        # can never import a stray .py sitting next to it.
        "--paths", ROOT,
        # Local hook overrides take precedence over pyinstaller-hooks-contrib.
        "--additional-hooks-dir", os.path.join(ROOT, "pyinstaller-hooks"),
    ]
    for mod in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", mod]
    model = silero_model()
    if model:
        # Landing name is silero_vad.onnx at the bundle root; audio.py looks
        # for it there first.
        cmd += ["--add-data", f"{model}{os.pathsep}."]
        print(f"[build] bundling Silero VAD from {os.path.basename(model)}")
    else:
        print("[build] WARNING: silero-vad not installed; the exe will fall "
              "back to webrtcvad, which cannot tell the game from a person")

    for pkg in COLLECT_ALL:
        cmd += ["--collect-all", pkg]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(ENTRY)

    print("[build] " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        sys.exit(f"[build] PyInstaller failed with exit code {rc}")

    exe = os.path.join(ROOT, "dist", f"{NAME}.exe")
    if not os.path.isfile(exe):
        sys.exit("[build] PyInstaller reported success but produced no exe")

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"[build] done: dist/{NAME}.exe ({size_mb:.0f} MB)")


if __name__ == "__main__":
    main()
