"""Check a built exe for anything that identifies the machine that built it.

    .venv\\Scripts\\python tools\\leak_scan.py dist\\CSVoiceCaptions.exe

The exe is published, so it must not carry the builder's user name, home or
AppData paths, the source checkout's path, the machine name or the git
identity. A plain text search of the file misses most of what is in it: the
Python code, the libraries and the bundled data are zlib-compressed inside.
So this unpacks every member -- each DLL, each compiled module of the Python
archive, each file of the zipped standard library -- and searches all of it,
in both the byte and the UTF-16 encodings Windows binaries use.

Exits 1 and lists every hit if anything is found. build.py --release runs it.
"""
import getpass
import io
import marshal
import os
import re
import socket
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(key):
    try:
        return subprocess.run(["git", "config", key], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def needles():
    """(label, compiled regex) for everything that must not appear."""
    values = []                      # (label, value, whole_word)
    paths = {os.environ.get(k) for k in ("USERPROFILE", "LOCALAPPDATA", "APPDATA",
                                         "TEMP", "TMP")} | {ROOT}
    for p in sorted(x for x in paths if x):
        for form in sorted({p, p.replace("\\", "/"), p.replace("\\", "\\\\")}):
            values.append((f"path {form}", form, False))
    for label, value, whole_word in (
            ("user name", getpass.getuser(), False),
            ("git email", _git("user.email"), False),
            ("git name", _git("user.name"), True),
            ("machine name", os.environ.get("COMPUTERNAME", ""), True),
            ("host name", socket.gethostname(), True)):
        if value and len(value) >= 3:
            values.append((f"{label} {value!r}", value, whole_word))
    # Each case-insensitive, in UTF-8 and again in UTF-16LE (wide strings).
    # Built from the raw value, not from an escaped pattern, so punctuation
    # like ":" and "\" gets its null byte too.
    compiled = []
    for label, value, whole_word in values:
        # A short name (a three-letter machine name, say) turns up by pure
        # chance in hundreds of megabytes of compressed data and bytecode --
        # 104 such matches in one build, every one random bytes. It only counts when
        # it sits inside readable text, the way a real name in a path or a
        # string would.
        short = len(value) < 6
        for enc, suffix in (("utf-8", ""), ("utf-16-le", " (UTF-16)")):
            if short and enc != "utf-8":
                continue
            pat = re.escape(value.encode(enc))
            if whole_word and enc == "utf-8":
                pat = rb"(?<![A-Za-z0-9])" + pat + rb"(?![A-Za-z0-9])"
            compiled.append((label + suffix, re.compile(pat, re.IGNORECASE), short))
    return compiled


_TEXT = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def _in_text(blob, start, end, width=6):
    """True if the bytes around a match read as text, not binary."""
    around = blob[max(0, start - width):start] + blob[end:end + width]
    return len(around) >= width and all(b in _TEXT for b in around)


def _scan(blob, where, pats, hits):
    for label, rx, short in pats:
        for m in rx.finditer(blob):
            if short and not _in_text(blob, m.start(), m.end()):
                continue
            ctx = blob[max(0, m.start() - 40):m.end() + 40]
            hits.append((where, label, ctx.decode("utf-8", "replace").replace("\x00", "")))
            break                       # one report per thing per member is enough


def scan(exe):
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    pats = needles()
    hits, members = [], 0
    with open(exe, "rb") as f:
        _scan(f.read(), "exe (raw bytes)", pats, hits)
    arc = CArchiveReader(exe)
    names = list(arc.toc.keys()) if isinstance(arc.toc, dict) else [e[-1] for e in arc.toc]
    for name in names:
        try:
            data = arc.extract(name)
        except Exception:  # noqa: BLE001
            continue
        if not data:
            continue
        members += 1
        _scan(data, name, pats, hits)
        if name.endswith(".pyz"):
            with tempfile.NamedTemporaryFile(suffix=".pyz", delete=False) as t:
                t.write(data)
            try:
                pyz = ZlibArchiveReader(t.name)
                mods = list(pyz.toc.keys()) if isinstance(pyz.toc, dict) else [e[0] for e in pyz.toc]
                for mod in mods:
                    code = pyz.extract(mod)
                    code = code[1] if isinstance(code, tuple) else code
                    if code is not None:
                        members += 1
                        _scan(marshal.dumps(code), f"{name}:{mod}", pats, hits)
            finally:
                os.unlink(t.name)
        elif data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for zi in z.infolist():
                    members += 1
                    _scan(z.read(zi), f"{name}:{zi.filename}", pats, hits)
    return hits, members, [label for label, _, _ in pats if "UTF-16" not in label]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    hits, members, looked_for = scan(sys.argv[1])
    print(f"[leak-scan] searched the exe and {members} members inside it for:")
    for label in looked_for:
        print(f"[leak-scan]   {label}")
    if hits:
        print(f"[leak-scan] FOUND {len(hits)}:")
        for where, label, ctx in hits:
            print(f"[leak-scan]   {where}: {label}  ...{ctx}...")
        sys.exit(1)
    print("[leak-scan] clean: none of it appears anywhere")


if __name__ == "__main__":
    main()
