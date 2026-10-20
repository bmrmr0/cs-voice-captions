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
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

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

# OpenVINO loads its core, its CPU/NPU plugins and the tokenizers
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
    # Silero runs on OpenVINO; the silero_vad package (and the onnxruntime it
    # imports) is only where the build finds the model file.
    "silero_vad", "onnxruntime",
    # An optional accelerator for the one-time model download; without it
    # huggingface_hub downloads over plain HTTPS.
    "hf_xet",
]

# Files that the packages above bring along but the app never loads, dropped
# after analysis (paths inside the bundle, matched as regexes). About 45 MB.
DROP_FILES = [
    # The GPU is never used: the game is running on it. Its plugin and its
    # kernel cache, plus the import library only a C++ linker reads.
    r"^openvino/libs/openvino_intel_gpu_plugin\.dll$",
    r"^openvino/libs/cache\.json$",
    r"^openvino/libs/openvino\.lib$",
    # Importers for model formats the app never reads. It loads OpenVINO IR
    # (Whisper) and ONNX (Silero), so the ir and onnx frontends stay.
    r"^openvino/libs/openvino_(tensorflow|tensorflow_lite|pytorch|paddle|jax)_frontend\.dll$",
    r"^openvino/frontend/(tensorflow|pytorch|paddle|jax)/",
    # Qt pieces for QML, Quick, PDF, OpenGL software rendering and the
    # on-screen keyboard; the app is plain widgets.
    r"^PySide6/opengl32sw\.dll$",
    r"^PySide6/Qt6(Pdf|Qml\w*|Quick\w*|VirtualKeyboard)\.dll$",
    r"^PySide6/plugins/(platforminputcontexts|qmltooling|generic|networkinformation)/",
    r"^PySide6/plugins/imageformats/q(pdf|svg)\.dll$",
    r"^PySide6/plugins/platforms/q(direct2d|minimal|offscreen)\.dll$",
    # Qt's own translations of its dialogs; the app's text is English.
    r"^PySide6/translations/",
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


def make_icon():
    """(Re)generate assets/icon.ico and return its path."""
    rc = subprocess.call([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")],
                         cwd=ROOT)
    path = os.path.join(ROOT, "assets", "icon.ico")
    if rc != 0 or not os.path.isfile(path):
        sys.exit("[build] could not generate the icon")
    return path


def version_file():
    """The exe's Windows file properties, written to a temp file for
    PyInstaller. Product name, version and licence only -- no company or
    personal name, since the exe is published."""
    sys.path.insert(0, ROOT)
    from version import __version__
    parts = [int(x) for x in __version__.split(".")] + [0, 0, 0]
    nums = tuple(parts[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'CS Voice Captions'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'CS Voice Captions'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    fd, path = tempfile.mkstemp(suffix="-version.txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path, __version__


def add_file_filter(spec):
    """Insert the DROP_FILES filter into the spec, after the analysis."""
    with open(spec, encoding="utf-8") as f:
        text = f.read()
    anchor = "pyz = PYZ("
    if anchor not in text:
        sys.exit("[build] unexpected spec layout; cannot apply DROP_FILES")
    block = f"""
import re as _re
_drop = _re.compile("|".join({DROP_FILES!r}), _re.IGNORECASE)
def _keep(entry):
    return not _drop.search(entry[0].replace("\\\\", "/"))
_dropped = [e for e in a.binaries + a.datas if not _keep(e)]
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
print(f"[build] dropped {{len(_dropped)}} unused files")
"""
    with open(spec, "w", encoding="utf-8") as f:
        f.write(text.replace(anchor, block + anchor, 1))


def clean(dist="dist"):
    """Remove previous build output. Also the point at which any stale tree
    from another machine (with its paths baked into the .toc files) is gone."""
    for d in ("build", dist):
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
    ap.add_argument("--release", action="store_true",
                    help="build the published exe into release/: leak-scanned, "
                         "with a SHA-256 checksum file. Fails if the scan finds "
                         "anything identifying this machine")
    ap.add_argument("--dist", default="dist",
                    help="output folder. Building somewhere other than dist/ "
                         "lets the copy in dist/ keep running meanwhile; "
                         "Windows won't let a running exe be replaced")
    args = ap.parse_args()
    if args.release:
        args.dist = "release"

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing. Run:\n"
                 "  pip install -r requirements-build.txt")

    if not args.no_clean:
        clean(args.dist)
    ver_file, version = version_file()

    # Written as a spec file first, so DROP_FILES can be applied between
    # PyInstaller's analysis and the bundling; the command line can only
    # exclude whole Python modules.
    cmd = [
        sys.executable, "-m", "PyInstaller.utils.cliutils.makespec",
        "--specpath", ROOT,
        "--onefile",
        "--name", NAME,
        # Drawn by app_icon.py, the same code the running app uses, so the
        # exe's icon and the window/tray icon can't drift apart.
        "--icon", make_icon(),
        "--version-file", ver_file,
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

    # The slang dictionary ships inside the exe; glossary.py looks for it at
    # the bundle root, and merges the user's own copy over it.
    cmd += ["--add-data", f"{os.path.join(ROOT, 'slang.json')}{os.pathsep}."]

    for pkg in COLLECT_ALL:
        cmd += ["--collect-all", pkg]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(ENTRY)

    print("[build] " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        sys.exit(f"[build] writing the spec failed with exit code {rc}")
    spec = os.path.join(ROOT, f"{NAME}.spec")
    add_file_filter(spec)

    rc = subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                          "--distpath", os.path.join(ROOT, args.dist), spec], cwd=ROOT)
    if rc != 0:
        sys.exit(f"[build] PyInstaller failed with exit code {rc}")

    exe = os.path.join(ROOT, args.dist, f"{NAME}.exe")
    if not os.path.isfile(exe):
        sys.exit("[build] PyInstaller reported success but produced no exe")

    size_mb = os.path.getsize(exe) / (1024 * 1024)
    print(f"[build] done: {args.dist}/{NAME}.exe ({size_mb:.0f} MB), version {version}")
    try:
        os.remove(ver_file)
    except OSError:
        pass

    if args.release:
        release(exe, version)


def release(exe, version):
    """Leak-scan the exe, then write its checksum. Refuses to finish if the
    scan finds the builder's name, paths or machine in it."""
    print("[build] scanning the exe for anything identifying this machine...")
    rc = subprocess.call([sys.executable, os.path.join(ROOT, "tools", "leak_scan.py"), exe],
                         cwd=ROOT)
    if rc != 0:
        os.remove(exe)
        sys.exit("[build] RELEASE BLOCKED: the exe contains private data (see above); "
                 "it has been deleted")
    digest = hashlib.sha256()
    with open(exe, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    sums = os.path.join(os.path.dirname(exe), "SHA256SUMS.txt")
    with open(sums, "w", encoding="ascii", newline="\n") as f:
        f.write(f"{digest.hexdigest()}  {os.path.basename(exe)}\n")
    print(f"[build] release {version} ready in {os.path.relpath(os.path.dirname(exe), ROOT)}/: "
          f"{os.path.basename(exe)} + SHA256SUMS.txt")


if __name__ == "__main__":
    main()
