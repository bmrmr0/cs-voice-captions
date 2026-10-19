"""One copy of the app at a time.

The app lives in the tray, so it is easy to start again without noticing it
is already running -- and a second copy was not harmless. It captured the
same game, so every caption appeared twice; it loaded the speech models a
second time (two copies held ~4 GB); and on startup it truncated the shared
log file the first copy was still writing to.

So a second launch talks to the first before doing anything else -- before
the log file is opened, in particular -- and one of two things happens:

  * it is the same build: the running copy brings its captions window
    forward (what someone double-clicking the exe wanted) and the new launch
    exits;
  * it is a newer build: the running copy quits and the new one takes its
    place. Without this, starting an updated exe while the old one sat in
    the tray just showed the old window again, and the update never ran.
"""
import os
import sys
import time

MUTEX_NAME = "Local\\CSVoiceCaptions.SingleInstance"
SERVER_NAME = "CSVoiceCaptions.ShowWindow"

_ERROR_ALREADY_EXISTS = 183
_mutex = None          # held for the life of the process; Windows frees it on exit


def _stamp():
    """When this build was made, as far as the running copy can tell: the
    exe's modification time, read once at startup (the file may be renamed
    or replaced while we run). 0 when running from source."""
    if not getattr(sys, "frozen", False):
        return 0.0
    try:
        return os.path.getmtime(sys.executable)
    except OSError:
        return 0.0


BUILD_STAMP = _stamp()


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, k32


def acquire(name=MUTEX_NAME):
    """Take the one-copy lock. False if another copy holds it.

    A named mutex rather than a lock file: Windows releases it however the
    process ends, so a crash can never leave the app refusing to start."""
    global _mutex
    if not sys.platform.startswith("win"):
        return True
    try:
        ctypes, k32 = _kernel32()
        handle = k32.CreateMutexW(None, False, name)
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)       # don't keep it alive on the holder's behalf
            return False
        _mutex = handle
        return True
    except Exception:  # noqa: BLE001
        return True    # if the check itself fails, err on the side of starting


def already_running(name=MUTEX_NAME):
    return not acquire(name)


def wait_for_lock(name=MUTEX_NAME, timeout_s=20.0):
    """After asking the old copy to quit: take the lock once it has gone."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if acquire(name):
            return True
        time.sleep(0.25)
    return False


def handshake(name=SERVER_NAME, stamp=None, timeout_ms=2000):
    """From a second launch: introduce ourselves to the running copy.

    Returns its answer -- "show" (it brought its window forward; we should
    exit) or "bye" (we are newer; it is quitting and we take over) -- or
    None if it couldn't be reached."""
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalSocket
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
        sock = QLocalSocket()
        sock.connectToServer(name)
        if not sock.waitForConnected(timeout_ms):
            return None
        sock.write(f"hello {BUILD_STAMP if stamp is None else stamp}\n".encode())
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        answer = None
        if sock.waitForReadyRead(timeout_ms):
            answer = bytes(sock.readAll()).decode(errors="replace").strip() or None
        sock.disconnectFromServer()
        # A copy from before this handshake existed reads nothing and simply
        # shows its window; to us that looks like no answer.
        return answer or "show"
    except Exception:  # noqa: BLE001
        return None


def ask_running_copy_to_show(name=SERVER_NAME, timeout_ms=1500):
    return handshake(name, timeout_ms=timeout_ms) is not None


def listen(on_show, on_replace, parent=None, name=SERVER_NAME, stamp=None):
    """In the running copy: answer later launches. A newer build gets "bye"
    and `on_replace` is called (it should quit the app); anything else gets
    "show" and `on_show`. Keep the returned server referenced."""
    from PySide6.QtNetwork import QLocalServer
    mine = BUILD_STAMP if stamp is None else stamp
    QLocalServer.removeServer(name)        # clear a stale endpoint left by a crash
    server = QLocalServer(parent)

    def accept():
        conn = server.nextPendingConnection()
        while conn is not None:
            theirs = 0.0
            if conn.waitForReadyRead(1000):
                parts = bytes(conn.readAll()).decode(errors="replace").split()
                if len(parts) == 2 and parts[0] == "hello":
                    try:
                        theirs = float(parts[1])
                    except ValueError:
                        pass
            newer = mine > 0 and theirs > mine + 1     # a second of slack for clocks
            conn.write(b"bye\n" if newer else b"show\n")
            conn.flush()
            conn.waitForBytesWritten(1000)
            conn.disconnectFromServer()
            if newer:
                print("[main] a newer build was started; handing over to it")
                on_replace()
                return
            on_show()
            conn = server.nextPendingConnection()

    server.newConnection.connect(accept)
    if not server.listen(name):
        print(f"[main] could not listen for later launches: {server.errorString()}")
    return server

