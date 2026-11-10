"""Real PTY session (POSIX): fork/exec a program attached to a pseudo-terminal."""
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
from pathlib import Path

from .vt import VTScreen


def _inject_app_pythonpath(env):
    """Ensure Android child shells can import the NDX package shipped with the app."""
    if "ANDROID_ARGUMENT" in env or "ANDROID_PRIVATE" in env or "ANDROID_APP_PATH" in env:
        app_root = str(Path(__file__).resolve().parents[2])
        entries = [item for item in env.get("PYTHONPATH", "").split(os.pathsep) if item]
        if app_root not in entries:
            env["PYTHONPATH"] = os.pathsep.join([app_root] + entries)
    return env


class PtySession:
    def __init__(self, argv, env=None, cwd=None, rows=24, cols=80, on_update=None, scrollback=2000, on_data=None):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.screen = VTScreen(rows, cols, scrollback)
        self.on_update = on_update or (lambda: None)
        self.on_data = on_data          # optional raw-bytes hook (used by the Terminal Bridge)
        self.proc = None
        self.master = None
        self.exit_code = None
        self._lock = threading.Lock()
        self.history = []        # commands typed (line-buffered by the UI)

    def start(self):
        master, slave = pty.openpty()
        self._set_size(slave, self.screen.rows, self.screen.cols)
        env = dict(os.environ)
        env.update(self.env or {})
        # A child shell launched by NDX must be able to import the bundled package.
        _inject_app_pythonpath(env)
        env.setdefault("TERM", "xterm-256color")
        env["COLUMNS"], env["LINES"] = str(self.screen.cols), str(self.screen.rows)

        def child_setup():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self.proc = subprocess.Popen(self.argv, stdin=slave, stdout=slave, stderr=slave, cwd=self.cwd,
                                         env=env, preexec_fn=child_setup, close_fds=True)
        finally:
            os.close(slave)
        self.master = master
        threading.Thread(target=self._reader, name="ndx-pty", daemon=True).start()
        return self

    @staticmethod
    def _set_size(fd, rows, cols):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _reader(self):
        fd = self.master
        while True:
            try:
                r, _, _ = select.select([fd], [], [], 0.5)
                if not r:
                    if self.proc.poll() is not None:
                        break
                    continue
                data = os.read(fd, 8192)
            except OSError:
                break
            if not data:
                break
            if self.on_data:
                try:
                    self.on_data(data)
                except Exception:
                    pass
            with self._lock:
                self.screen.feed(data)
                resp, self.screen.responses = self.screen.responses, []
            for rbytes in resp:
                self.write(rbytes)
            self.on_update()
        self.exit_code = self.proc.wait()
        try:
            os.close(fd)
        except OSError:
            pass
        self.master = None
        self.on_update()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if self.master is not None and data:
            try:
                os.write(self.master, data)
            except OSError:
                pass

    def resize(self, rows, cols):
        if self.master is None or rows < 2 or cols < 10:
            return
        with self._lock:
            self.screen.resize(rows, cols)
        try:
            self._set_size(self.master, rows, cols)
            if self.proc and self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def snapshot_markup(self, **kw):
        with self._lock:
            return self.screen.markup(**kw)

    def terminate(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGHUP)
            except (OSError, ProcessLookupError):
                self.proc.terminate()
