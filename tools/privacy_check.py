"""Refuse commits that would publish someone's private data.

Run by the git hooks in tools/githooks (enable once per clone with
`git config core.hooksPath tools/githooks`):

    privacy_check.py --staged          lines a commit adds
    privacy_check.py --message FILE    a commit message

Why it exists: while debugging team chat, a real player's in-game name and
his chat lines were copied out of CS2's log into a code comment and a commit
message, and published. This makes that, and anything like it, fail the
commit instead:

  * text copied from CS2's chat log. Every name there is followed by an
    invisible left-to-right mark (U+200E), and a living player's location by
    a small at-sign (U+FE6B). Nothing typed by hand contains them, so they
    give away a pasted chat line whoever the player was;
  * the names of players in this machine's current CS2 log and app log;
  * the builder's identity: user name, home/AppData/temp paths, the checkout
    path, the machine name (the same list the exe's leak scan uses);
  * token- and key-shaped secrets.

Examples in code, comments, docs and commit messages should be made up.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

LOG_MARKS = {"\u200e": "invisible mark CS2 puts after player names (U+200E)",
             "\ufe6b": "small at-sign CS2 puts before a player's location (U+FE6B)"}

SECRETS = [
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("Hugging Face token", r"hf_[A-Za-z0-9]{30,}"),
    ("API key", r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("password or key in code",
     r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]


def _git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    return r.stdout.decode("utf-8", "replace")


def _distinctive(name):
    """Gamer tags worth matching. A plain word ("Fuzzy", "cancelled") would
    block innocent text; a tag with digits, symbols, inner capitals, spaces or
    non-Latin letters will not turn up by accident."""
    if len(name) < 4:
        return False
    return bool(re.search(r"[0-9_$@#!?*^~|\\/.\-\[\]()<>]|[a-z][A-Z]|\s|[^\x00-\x7f]", name))


def player_names():
    """Names in this machine's current CS2 chat log and the app's own log.
    Read, never stored: CS2 rewrites its log on every launch."""
    names = set()
    sources = []
    try:
        import chat_log
        path = chat_log.find_console_log("")
        if path:
            sources.append(path)
    except Exception:  # noqa: BLE001
        pass
    try:
        import config
        sources.append(os.path.join(config.data_dir(), "cs-voice-captions.log"))
    except Exception:  # noqa: BLE001
        pass
    for src in sources:
        try:
            text = open(src, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r"\[(?:ALL|TEAM|CT|T)\]\s+(.+?)(?:\u200e|: )", text):
            names.add(m.group(1).split("\ufe6b")[0].replace("\u200e", "").strip())
        for m in re.finditer(r"\[chat:[A-Z]+\]\s+(.+?):\s", text):
            names.add(m.group(1).strip())
    me = _git("config", "user.name").strip().lower()
    return sorted(n for n in names if _distinctive(n) and n.lower() != me)


def identity_patterns():
    """The exe leak scan's list, minus the git name and email: those are
    public on every commit by the owner's choice."""
    import leak_scan
    out = []
    for label, rx, short in leak_scan.needles():
        if label.startswith(("git email", "git name")) or "UTF-16" in label:
            continue
        out.append((label, re.compile(rx.pattern.decode("utf-8", "replace"), re.IGNORECASE), short))
    return out


def check(chunks):
    """chunks: [(where, text)]. Returns [(where, what, excerpt)]."""
    ident = identity_patterns()
    names = player_names()
    secrets = [(label, re.compile(p)) for label, p in SECRETS]
    problems = []

    def add(where, what, text, start, end):
        excerpt = text[max(0, start - 30):end + 30].replace("\n", " ")
        problems.append((where, what, excerpt))

    for where, text in chunks:
        for ch, what in LOG_MARKS.items():
            i = text.find(ch)
            if i >= 0:
                add(where, f"text pasted from CS2's chat log: {what}", text, i, i + 1)
        for label, rx, short in ident:
            for m in rx.finditer(text):
                if short:
                    a, b = text[max(0, m.start() - 1):m.start()], text[m.end():m.end() + 1]
                    if (a and a.isalnum()) or (b and b.isalnum()):
                        continue
                add(where, label, text, m.start(), m.end())
                break
        for n in names:
            i = text.find(n)
            if i >= 0:
                add(where, f"a player's name from this machine's chat log: {n!r}",
                    text, i, i + len(n))
        for label, rx in secrets:
            m = rx.search(text)
            if m:
                add(where, label, text, m.start(), m.end())
    return problems


def staged_chunks():
    """Added lines of the staged diff, per file. Unchanged lines (such as the
    parser's own escaped copies of those marks) are not this commit's doing."""
    diff = _git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR")
    chunks, current, lines = [], None, []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            if current and lines:
                chunks.append((current, "\n".join(lines)))
            current, lines = line[6:] if line.startswith("+++ b/") else line[4:], []
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    if current and lines:
        chunks.append((current, "\n".join(lines)))
    return chunks


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--staged":
        chunks, what = staged_chunks(), "this commit"
    elif len(sys.argv) >= 3 and sys.argv[1] == "--message":
        text = open(sys.argv[2], encoding="utf-8", errors="replace").read()
        text = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
        chunks, what = [("commit message", text)], "this commit message"
    else:
        sys.exit(__doc__)
    problems = check(chunks)
    if not problems:
        return
    print(f"\n[privacy] {what} would publish private data:\n", file=sys.stderr)
    for where, kind, excerpt in problems:
        print(f"  {where}: {kind}\n      ...{excerpt}...", file=sys.stderr)
    print("\n[privacy] Replace it with made-up examples, then commit again.\n",
          file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
