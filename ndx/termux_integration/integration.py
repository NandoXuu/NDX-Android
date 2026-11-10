"""TermuxIntegration: optional, honest bridge to the separate Termux app.

States (status()["state"]): unavailable | not_installed | unknown_visibility | permission_required |
no_response | ready. NDX works without Termux; nothing here is required at startup."""
import re
import threading
import time
from dataclasses import dataclass

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..core.registry import Module
from .transport import TERMUX_API_PACKAGE, TERMUX_PACKAGE, default_transport

TERMUX_PREFIX = "/data/data/com.termux/files/usr"
TERMUX_HOME = "/data/data/com.termux/files/home"
CMD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}$")
PROBE_TTL = 60.0

GUIDANCE = {
    "unavailable": ["Termux integration only exists on Android."],
    "not_installed": ["Install Termux (from F-Droid or the Termux GitHub releases; the Play Store build is outdated)."],
    "unknown_visibility": ["Android 11+ hides other apps from NDX unless they are declared in the manifest <queries>.",
                           "Use 'Test connection': NDX will try the RUN_COMMAND intent anyway."],
    "permission_required": ["Grant NDX the permission 'Run commands in Termux environment' "
                            "(Android Settings > Apps > NDX > Permissions > Additional permissions).",
                            "Then open Termux and add `allow-external-apps = true` to ~/.termux/termux.properties "
                            "and restart Termux."],
    "no_response": ["Open Termux once so its environment is initialised.",
                    "Add `allow-external-apps = true` to ~/.termux/termux.properties, then run `termux-reload-settings` "
                    "or restart Termux.",
                    "Make sure Termux is not battery-restricted; Android 12+ may block background starts."],
    "ready": ["Termux:API (separate app + `pkg install termux-api`) is only needed for termux-* commands."],
}


@dataclass
class TermuxResult:
    exit_code: object = None
    stdout: str = ""
    stderr: str = ""
    err: object = None
    errmsg: str = ""
    timed_out: bool = False

    @property
    def ok(self):
        return self.exit_code == 0 and not self.timed_out and self.err in (None, -1, 0)


def _to_result(d):
    return TermuxResult(d.get("exit_code"), d.get("stdout", ""), d.get("stderr", ""), d.get("err"),
                        d.get("errmsg", ""), bool(d.get("timed_out")))


class TermuxIntegration(Module):
    name = "termux"
    title = "Termux integration"

    def __init__(self, bus=None, config=None, audit=None, transport=None):
        self.bus, self.config = bus, config
        self.audit = audit or (lambda *a, **k: None)
        self.transport = transport or default_transport()
        self._lock = threading.Lock()
        self._state = None
        self._cmd_cache = {}

    # NDX must start without Termux: nothing is probed at startup
    def start(self, core):
        self.status = "running"

    def enabled(self):
        return not self.config or bool(self.config.get("termux.enabled", True))

    def _static_state(self):
        t = self.transport
        inst = t.package_installed(TERMUX_PACKAGE)
        if inst is False:
            return ("unavailable" if type(t).__name__ == "UnavailableTransport" else "not_installed"), \
                   getattr(t, "reason", "Termux package not found")
        if inst is None:
            return "unknown_visibility", "cannot tell whether Termux is installed (package visibility)"
        if t.permission_granted() is False:
            return "permission_required", "com.termux.permission.RUN_COMMAND is not granted"
        return None, ""

    def get_status(self, probe=False, force=False):
        """dict(state, detail, guidance, termux_api). Cheap unless probe=True (runs one harmless command)."""
        state, detail = self._static_state()
        if state is None:
            with self._lock:
                cached = self._state
            if cached and not force and (probe is False or time.time() - cached["time"] < PROBE_TTL):
                state, detail = cached["state"], cached["detail"]
            elif probe:
                state, detail = self._probe()
            else:
                state, detail = "no_response", "not tested yet: use Test connection"
        elif state == "unknown_visibility" and probe:
            s2, d2 = self._probe()
            if s2 == "ready":
                state, detail = s2, d2
        api = self.transport.package_installed(TERMUX_API_PACKAGE)
        return {"state": state, "detail": detail, "guidance": list(GUIDANCE.get(state, [])) +
                (GUIDANCE["ready"] if state == "ready" else []), "termux_api": api}

    def status_dict(self):
        return self.get_status()

    def _probe(self):
        try:
            r = self._exec(TERMUX_PREFIX + "/bin/sh", ["-c", "echo ndx-ok"], None, 15)
        except NDXError as e:
            res = ("unavailable", str(e))
        else:
            if r.ok and "ndx-ok" in r.stdout:
                res = ("ready", "Termux answered the test command")
            else:
                res = ("no_response", r.errmsg or "no usable answer (exit %s)" % r.exit_code)
        with self._lock:
            self._state = {"state": res[0], "detail": res[1], "time": time.time()}
        if self.bus:
            self.bus.publish("termux.changed", {"state": res[0]}, "termux")
        return res

    # ---- Module-facing status used by the toolchain installer ({"state": ..., "detail": ...})
    def status_for_toolchain(self):
        s = self.get_status()
        return {"state": s["state"], "detail": s["detail"]}

    def request_permission(self):
        return self.transport.request_permission()

    # ---- execution
    def _exec(self, path, args, cwd, timeout):
        if not self.enabled():
            raise ToolUnavailable("Termux integration is disabled in settings")
        return _to_result(self.transport.execute(path, list(args), cwd, timeout))

    def run(self, command, args=(), cwd=None, timeout=60):
        """Run a Termux command by NAME (looked up in Termux's own bin dir) with an argv list; never a shell string."""
        if not isinstance(command, str) or not CMD_RE.match(command):
            raise ValidationError("invalid Termux command name: %r (use a plain command name, no paths)" % (command,))
        args = [a for a in args]
        if not all(isinstance(a, str) and "\0" not in a for a in args):
            raise ValidationError("arguments must be strings")
        if cwd is not None and not (isinstance(cwd, str) and cwd.startswith(("/data/data/com.termux/", "/storage/",
                                                                              "/sdcard/"))):
            raise ValidationError("cwd must be a Termux path or shared storage (host paths are not visible to Termux)")
        st = self.get_status()
        if st["state"] not in ("ready", "unknown_visibility", "no_response"):
            raise ToolUnavailable("Termux is %s: %s" % (st["state"], "; ".join(st["guidance"][:1])))
        self.audit("termux.run", command=command, argc=len(args))
        return self._exec(TERMUX_PREFIX + "/bin/" + command, args, cwd, timeout)

    def has_command(self, name):
        """True only when Termux is known to be ready AND the command exists (cached; never raises)."""
        if not CMD_RE.match(name or ""):
            return False
        with self._lock:
            st = self._state
            hit = self._cmd_cache.get(name)
        if not st or st["state"] != "ready":
            return False
        if hit and time.time() - hit[1] < PROBE_TTL:
            return hit[0]
        try:
            r = self._exec(TERMUX_PREFIX + "/bin/sh", ["-c", 'command -v -- "$1"', "_", name], None, 15)
            ok = r.ok and bool(r.stdout.strip())
        except NDXError:
            ok = False
        with self._lock:
            self._cmd_cache[name] = (ok, time.time())
        return ok

    def diagnostics(self):
        s = self.get_status()
        return {"status": self.status, "error": "", "state": s["state"], "detail": s["detail"]}
