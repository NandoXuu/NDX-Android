import itertools

from ..core.registry import Module
from .pty_session import PtySession


class TerminalManager(Module):
    name = "terminal"
    title = "Terminal"

    def __init__(self, shell_env, bus, config, audit=None):
        self.shell_env, self.bus, self.config = shell_env, bus, config
        self.audit = audit or (lambda *a, **k: None)
        self.sessions = {}
        self._ids = itertools.count(1)
        self.activity = []       # recent session starts for the dashboard

    def create(self, profile=None, cwd=None, rows=24, cols=80, on_update=None):
        profile = profile or self.shell_env.default_profile(cwd)
        if profile is None:
            raise RuntimeError("no shell available on this device")
        sid = next(self._ids)
        s = PtySession(profile.argv, profile.env, cwd or profile.cwd or None, rows, cols, on_update,
                       self.config.get("terminal.scrollback", 2000))
        s.title = "%s #%d" % (profile.title, sid)
        s.start()
        self.sessions[sid] = s
        self.audit("terminal.start", profile=profile.id, argv=profile.argv)
        self.activity.append(s.title)
        self.activity = self.activity[-10:]
        self.bus.publish("terminal.started", {"id": sid, "title": s.title}, "terminal")
        return sid, s

    def close(self, sid):
        s = self.sessions.pop(sid, None)
        if s:
            s.terminate()

    def stop(self):
        for sid in list(self.sessions):
            self.close(sid)
        self.status = "stopped"
