"""One copy of the app at a time.

The app lives in the tray, so it is easy to start again without noticing it
is already running -- and a second copy was not harmless. It captured the
same game, so every caption appeared twice; it loaded the speech models a
second time (two copies held ~4 GB); and on startup it truncated the shared
log file the first copy was still writing to.

Now a second launch hands over to the first: it asks the running copy to
bring its captions window forward, which is what someone double-clicking the
exe actually wanted, and exits. The check runs before anything else -- in
particular before the log file is opened.
"""
import sys

MUTEX_NAME = "Local\\CSVoiceCaptions.SingleInstance"
SERVER_NAME = "CSVoiceCaptions.ShowWindow"

_ERROR_ALREADY_EXISTS = 183
_mutex = None          # held for the life of the process; Windows frees it on exit


def already_running(name=MUTEX_NAME):
    """True if another copy holds the lock; otherwise take it and say False.

    A named mutex rather than a lock file: Windows releases it however the
    process ends, so a crash can never leave the app refusing to start."""
    global _mutex
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        _mutex = k32.CreateMutexW(None, False, name)
        return ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return False   # if the check itself fails, err on the side of starting


def ask_running_copy_to_show(name=SERVER_NAME, timeout_ms=1500):
    """Tell the copy that is already running to bring its window forward."""
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalSocket
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841
        sock = QLocalSocket()
        sock.connectToServer(name)
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True
    except Exception:  # noqa: BLE001
        return False


def listen_for_show(on_show, parent=None, name=SERVER_NAME):
    """In the copy that is running: call `on_show` (on the GUI thread) when a
    later launch asks for the window. Keep the returned server referenced."""
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(name)        # clear a stale endpoint left by a crash
    server = QLocalServer(parent)

    def accept():
        conn = server.nextPendingConnection()
        while conn is not None:
            conn.disconnectFromServer()
            conn = server.nextPendingConnection()
        on_show()

    server.newConnection.connect(accept)
    if not server.listen(name):
        print(f"[main] could not listen for later launches: {server.errorString()}")
    return server
