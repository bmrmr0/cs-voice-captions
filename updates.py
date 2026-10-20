"""Tell the user when a newer release is out.

One request on start, to GitHub's public "latest release" API for this
repository. Nothing about the user or their PC is sent beyond what any web
request carries, and nothing is downloaded or installed: the app only says a
new version exists and links to its page.
"""
import json
import re
import threading
import urllib.request

from version import __version__

REPO = "bmrmr0/cs-voice-captions"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"


def parse_version(text):
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0); None if it isn't one."""
    m = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*$", text or "")
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def latest_release(timeout=10):
    """(tag, page url) of the latest release, or None if it can't be told."""
    req = urllib.request.Request(API, headers={
        # GitHub refuses requests without a User-Agent.
        "User-Agent": f"CSVoiceCaptions/{__version__}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name") or ""
    url = data.get("html_url") or PAGE
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = PAGE                    # only ever link to this repo's pages
    return tag, url


def check_async(on_newer):
    """Look in the background; call on_newer(tag, url) -- from that thread --
    only if a newer version is out. Failures (offline, rate limit) are
    silent apart from the log: this is a courtesy, not a requirement."""
    def run():
        try:
            found = latest_release()
        except Exception as e:  # noqa: BLE001
            print(f"[updates] could not check: {e}")
            return
        if not found:
            return
        tag, url = found
        if newer(tag):
            print(f"[updates] {tag} is out (this is {__version__})")
            on_newer(tag, url)
        else:
            print(f"[updates] up to date ({__version__})")
    threading.Thread(target=run, daemon=True, name="update-check").start()
