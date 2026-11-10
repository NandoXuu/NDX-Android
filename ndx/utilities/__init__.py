import os
import shutil
import subprocess


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024


def human_duration(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return "%dh %02dm" % (h, m) if h else ("%dm %02ds" % (m, s) if m else "%ds" % s)


def which(cmd, extra_dirs=()):
    path = os.pathsep.join([str(d) for d in extra_dirs] + [os.environ.get("PATH", "")])
    return shutil.which(cmd, path=path)


def run_capture(argv, cwd=None, timeout=30, env=None):
    """Run a command without a shell; returns (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "command not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout


def match_bracket(text, index):
    """Return the index of the bracket matching the one at `index`, or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    rev = {v: k for k, v in pairs.items()}
    if index < 0 or index >= len(text):
        return -1
    ch = text[index]
    if ch in pairs:
        depth, step, end = 0, 1, len(text)
        target = pairs[ch]
    elif ch in rev:
        depth, step, end = 0, -1, -1
        target = rev[ch]
    else:
        return -1
    i = index
    while i != end:
        c = text[i]
        if c == ch:
            depth += 1
        elif c == target:
            depth -= 1
            if depth == 0:
                return i
        i += step
    return -1
