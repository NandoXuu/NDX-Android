"""ToolRunner: how SCRIPTS and plugins call tools (ndx.tools.run).

    result = ndx.tools.run("git", ["status"], environment="host")
    result = ndx.tools.run("clang", ["main.c", "-o", "main"], environment="ubuntu")

Differences from the Terminal Bridge (the trusted core API): the caller is identified, argument arrays are
validated (never a shell string), dangerous environment variables are refused, the working directory is confined,
and anything that is not the user's own action needs a capability (plugins: process.spawn) or the user's consent."""
import os
import re
import threading
from dataclasses import dataclass, field

from ..core.errors import NDXError, PermissionDenied, ToolUnavailable, ValidationError

CMD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}$")
FORBIDDEN_ENV = re.compile(r"^(PATH|LD_.*|DYLD_.*|PROOT_.*|NDX_.*|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|BASH_ENV|ENV|IFS)$")
SHELLS = {"sh", "ash", "bash", "zsh", "fish", "dash", "ksh", "busybox"}
MAX_ARGS, MAX_ARG_BYTES, MAX_TIMEOUT, DEFAULT_TIMEOUT = 256, 128 * 1024, 3600.0, 120.0
OUTPUT_CAP = 1024 * 1024
TRUSTED = ("core", "user", "ui")


@dataclass
class ToolResult:
    tool: str
    environment: str
    argv: list
    returncode: object = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    duration: float = 0.0
    error: str = ""
    truncated: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.returncode == 0 and not (self.timed_out or self.cancelled or self.error)

    @property
    def output(self):
        return self.stdout + self.stderr

    def check(self):
        """Raise NDXError unless the command succeeded (for scripts that prefer exceptions)."""
        if not self.ok:
            raise NDXError("%s failed (exit %s): %s" % (self.tool, self.returncode, (self.error or self.stderr).strip()[-300:]))
        return self

    def to_dict(self):
        return {"tool": self.tool, "environment": self.environment, "argv": self.argv, "returncode": self.returncode,
                "stdout": self.stdout, "stderr": self.stderr, "ok": self.ok, "timed_out": self.timed_out,
                "cancelled": self.cancelled, "duration": round(self.duration, 3), "error": self.error,
                "truncated": self.truncated}


class ToolRunner:
    def __init__(self, bridge, toolchain, permissions=None, consent=None, audit=None, config=None, paths=None):
        self.bridge, self.tc, self.permissions, self.consent, self.config, self.paths = bridge, toolchain, permissions, consent, config, paths
        self.audit = audit or (lambda *a, **k: None)
        self._grants = set()
        self._lock = threading.Lock()
        self.running = {}          # id -> cancel Event (monitoring / cancellation)
        self._ids = iter(range(1, 10 ** 9))

    # ---- validation
    def _validate(self, tool, args, env, timeout, cwd, caller):
        if not isinstance(tool, str) or not CMD_RE.match(tool):
            raise ValidationError("tool must be a plain command name (no path, no spaces): %r" % (tool,))
        if isinstance(args, str):
            raise ValidationError("args must be a list of strings, not a shell string")
        args = list(args or [])
        if len(args) > MAX_ARGS or not all(isinstance(a, str) and "\0" not in a for a in args):
            raise ValidationError("args must be at most %d strings without NUL bytes" % MAX_ARGS)
        if sum(len(a) for a in args) > MAX_ARG_BYTES:
            raise ValidationError("arguments are too long")
        env = dict(env or {})
        for k, v in env.items():
            if not isinstance(k, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) or not isinstance(v, str):
                raise ValidationError("invalid environment variable %r" % (k,))
            if caller not in TRUSTED and FORBIDDEN_ENV.match(k):
                raise PermissionDenied("scripts may not set %s" % k)
        cap = float(self.config.get("tools.max_timeout", MAX_TIMEOUT)) if self.config else MAX_TIMEOUT
        timeout = DEFAULT_TIMEOUT if timeout is None else float(timeout)
        if timeout <= 0:
            raise ValidationError("timeout must be positive")
        timeout = min(timeout, cap)
        if cwd is not None:
            cwd = os.path.realpath(str(cwd))
            if caller not in TRUSTED and self.paths is not None:
                roots = [os.path.realpath(str(self.paths.projects)), os.path.realpath(str(self.paths.cache))]
                if not any(cwd == r or cwd.startswith(r + os.sep) for r in roots):
                    raise PermissionDenied("scripts may only use a working directory inside the NDX projects or cache folders")
        return args, env, timeout, cwd

    def _authorize(self, caller, tool, args, environment):
        if caller in TRUSTED:
            return
        if caller.startswith("plugin:"):
            if self.permissions is None:
                raise PermissionDenied("no permission service")
            self.permissions.require(caller.split(":", 1)[1], "process.spawn")
            return
        key = (caller, tool, environment)
        with self._lock:
            if key in self._grants and not (self.config and self.config.get("tools.always_ask", False)):
                return
        if self.consent is None:
            raise PermissionDenied("running tools from scripts needs your approval and no approval channel is available")
        shell = tool in SHELLS
        line = " ".join([tool] + [a if len(a) < 60 else a[:57] + "..." for a in args[:12]]) + (" ..." if len(args) > 12 else "")
        ok = self.consent.request(
            "Let a script run '%s'?" % tool,
            ("A script (%s) wants to run a program on this device." % caller) +
            ("\nThis is a SHELL: it can run anything." if shell else ""),
            ["Command: " + line, "Environment: " + environment, "Approving allows this tool/environment for this session."])
        if not ok:
            self.audit("tool.denied", caller=caller, tool=tool, environment=environment)
            raise PermissionDenied("the user denied running '%s'" % tool)
        with self._lock:
            self._grants.add(key)

    def forget_grants(self):
        with self._lock:
            self._grants.clear()

    # ---- API
    def run(self, tool, args=(), environment="host", cwd=None, env=None, timeout=None, input=None,   # noqa: A002
            caller="script", cancel=None, on_output=None):
        args, env, timeout, cwd = self._validate(tool, args, env, timeout, cwd, caller)
        environment = environment or "host"
        self.bridge.backend_for(environment)           # validates the environment name before asking the user
        self._authorize(caller, tool, args, environment)
        cancel = cancel or threading.Event()
        rid = next(self._ids)
        self.running[rid] = cancel
        self.audit("tool.run", caller=caller, tool=tool, environment=environment, argc=len(args))
        try:
            res = self.bridge.run([tool] + args, environment, cwd, env, timeout, input, cancel, on_output,
                                  caller=caller, script=caller not in TRUSTED)
        finally:
            self.running.pop(rid, None)
        out, err, trunc = res.stdout, res.stderr, False
        if len(out) > OUTPUT_CAP:
            out, trunc = out[:OUTPUT_CAP], True
        if len(err) > OUTPUT_CAP:
            err, trunc = err[:OUTPUT_CAP], True
        return ToolResult(tool, environment, res.argv, res.returncode, out, err, res.timed_out, res.cancelled,
                          res.duration, res.error, trunc)

    def resolve_info(self, tool, environment="host"):
        if not isinstance(tool, str) or not CMD_RE.match(tool):
            raise ValidationError("invalid tool name")
        backend = self.bridge.backend_for(environment)
        ok, why = backend.available()
        info = {"tool": tool, "environment": environment, "available": ok, "reason": why, "found": None}
        if environment in ("host", None):
            r = self.tc.resolve(tool, runtime="host")
            info.update(found=bool(r), kind=r.kind if r else None, path=r.path if r else None,
                        version=self.tc.get_version(tool) if r else None)
        elif backend.id == "linux" and self.bridge.linux is not None:
            eid = environment.split(":", 1)[-1]
            hits = [p for e, p in self.bridge.linux.probe_command(tool) if e == eid]
            info.update(found=bool(hits), path=hits[0] if hits else None, kind="proot")
        return info

    def list_tools(self):
        return [{"id": r["id"], "name": r["name"], "installed": r["installed"], "resolved": r["resolved"],
                 "kind": r["kind"]} for r in self.tc.list()]

    def cancel_all(self):
        for ev in list(self.running.values()):
            ev.set()
