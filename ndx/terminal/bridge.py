"""NDX Terminal Bridge: ONE API to run commands / open interactive sessions in any execution environment.

    session = ndx.terminal.open(environment="ubuntu")
    session.write("clang main.c -o main\\n")
    output = session.read(timeout=2)

Environments (strings): "host" (NDX-managed/native executables), "android" (the Android system shell, its own
PATH), "termux" (through the Termux integration), "linux:<id>" or just "<id>" (a managed Linux environment),
"proot:/abs/rootfs" (a bare root filesystem). Each backend builds its OWN environment/PATH: nothing from one
runtime leaks into another.

Interactive sessions reuse terminal.pty_session.PtySession; non-interactive runs reuse terminal.process.run_process.
The bridge is the trusted core API: scripts must go through ndx.toolchain_manager.ToolRunner (consent/permissions)."""
import os
import posixpath
import re
import signal
import subprocess
import threading
import time
import itertools

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..core.paths import is_android
from ..core.registry import Module
from .process import CommandResult, kill_group, run_process
from .pty_session import PtySession

MAX_BUFFER = 4 * 1024 * 1024
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
ANDROID_PATH = "/system/bin:/system/xbin:/vendor/bin"


class BackendUnavailable(ToolUnavailable):
    pass


def _check_argv(argv):
    if isinstance(argv, str):
        raise ValidationError("argv must be a list of strings, not a shell string (no shell is ever used)")
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv):
        raise ValidationError("argv must be a non-empty list of strings")
    return list(argv)


def _check_env(env):
    env = dict(env or {})
    for k, v in env.items():
        if not isinstance(k, str) or not ENV_KEY_RE.match(k) or not isinstance(v, str) or "\0" in v:
            raise ValidationError("invalid environment variable: %r" % (k,))
    return env


def _check_cwd(cwd):
    if cwd is None:
        return None
    cwd = str(cwd)
    if not os.path.isabs(cwd) or not os.path.isdir(cwd):
        raise ValidationError("cwd must be an existing absolute directory: %s" % cwd)
    return cwd


# --------------------------------------------------------------------------------------------- sessions
class Session:
    """Unified handle: write/read/wait/status/cancel/terminate for PTY and pipe sessions."""
    _ids = itertools.count(1)

    def __init__(self, environment, backend_id, argv, cwd, timeout=None):
        self.id = next(self._ids)
        self.environment, self.backend_id, self.argv, self.cwd = environment, backend_id, list(argv), cwd
        self.started = time.time()
        self.timeout = timeout
        self.timed_out = False
        self.cancelled = False
        self._buf = []
        self._size = 0
        self._cv = threading.Condition()
        self._on_close = None
        self.pty = False

    # subclass hooks
    def _write(self, data):
        raise NotImplementedError

    def alive(self):
        raise NotImplementedError

    @property
    def exit_code(self):
        raise NotImplementedError

    def pid(self):
        return None

    def _kill(self, sig):
        raise NotImplementedError

    def resize(self, rows, cols):
        pass

    # shared behavior
    def _push(self, text):
        with self._cv:
            self._buf.append(text)
            self._size += len(text)
            while self._size > MAX_BUFFER and len(self._buf) > 1:
                self._size -= len(self._buf.pop(0))
            self._cv.notify_all()

    def write(self, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        if not self.alive():
            raise NDXError("session %d has ended (exit code %s)" % (self.id, self.exit_code))
        self._write(data)

    def read(self, timeout=0.0):
        """Everything received since the last read(); waits up to `timeout` s for the first data."""
        end = time.time() + timeout
        with self._cv:
            while not self._buf and self.alive() and time.time() < end:
                self._cv.wait(max(0.0, min(0.1, end - time.time())))
            out, self._buf, self._size = "".join(self._buf), [], 0
        return out

    def read_until(self, needle, timeout=10.0):
        got, end = "", time.time() + timeout
        while needle not in got and time.time() < end:
            got += self.read(0.1)
            if not self.alive():
                got += self.read(0.05)
                break
        return got

    def wait(self, timeout=None):
        end = None if timeout is None else time.time() + timeout
        while self.alive():
            if end is not None and time.time() >= end:
                return None
            time.sleep(0.02)
        return self.exit_code

    def status(self):
        alive = self.alive()
        state = "running" if alive else ("timed_out" if self.timed_out else "cancelled" if self.cancelled else "exited")
        return {"id": self.id, "state": state, "exit_code": None if alive else self.exit_code, "pid": self.pid(),
                "environment": self.environment, "backend": self.backend_id, "argv": self.argv,
                "duration": round(time.time() - self.started, 2), "pty": self.pty}

    def terminate(self, grace=2.0):
        """SIGTERM (SIGHUP for a PTY) then SIGKILL after `grace` seconds."""
        if not self.alive():
            return
        self.cancelled = True
        self._kill(signal.SIGTERM)
        end = time.time() + grace
        while self.alive() and time.time() < end:
            time.sleep(0.02)
        if self.alive():
            self._kill(signal.SIGKILL)

    cancel = terminate

    def close(self):
        self.terminate(0.5)
        if self._on_close:
            self._on_close(self)

    def _watchdog(self):
        if not self.timeout:
            return

        def run():
            end = self.started + self.timeout
            while self.alive():
                if time.time() >= end:
                    self.timed_out = True
                    self._kill(signal.SIGTERM)
                    time.sleep(1.5)
                    if self.alive():
                        self._kill(signal.SIGKILL)
                    return
                time.sleep(0.05)
        threading.Thread(target=run, daemon=True, name="ndx-session-watchdog").start()


class PtyBridgeSession(Session):
    """Interactive session on a real pseudo-terminal (the existing PtySession)."""

    def __init__(self, environment, backend_id, argv, env, cwd, rows, cols, on_update, timeout, scrollback):
        super().__init__(environment, backend_id, argv, cwd, timeout)
        self.pty = True
        self.pty_session = PtySession(argv, env, cwd, rows, cols, on_update, scrollback,
                                      on_data=lambda b: self._push(b.decode("utf-8", "replace")))
        self.pty_session.start()
        self._watchdog()

    @property
    def screen(self):
        return self.pty_session.screen

    def _write(self, data):
        self.pty_session.write(data)

    def alive(self):
        return self.pty_session.alive() or (self.pty_session.proc is not None and self.pty_session.exit_code is None
                                            and self.pty_session.master is not None)

    @property
    def exit_code(self):
        return self.pty_session.exit_code if self.pty_session.exit_code is not None else self.pty_session.proc.poll()

    def pid(self):
        return self.pty_session.proc.pid if self.pty_session.proc else None

    def resize(self, rows, cols):
        self.pty_session.resize(rows, cols)

    def _kill(self, sig):
        p = self.pty_session.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGHUP if sig == signal.SIGTERM else sig)
            except (OSError, ProcessLookupError):
                try:
                    p.send_signal(sig)
                except OSError:
                    pass


class PipeSession(Session):
    """Non-PTY session: separate stdin / stdout / stderr pipes."""

    def __init__(self, environment, backend_id, argv, env, cwd, timeout):
        super().__init__(environment, backend_id, argv, cwd, timeout)
        self._err = []
        try:
            self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
        except FileNotFoundError:
            raise ToolUnavailable("command not found: %s" % argv[0])
        except OSError as e:
            raise NDXError("cannot start %s: %s" % (argv[0], e))
        for stream, sink in ((self.proc.stdout, self._push), (self.proc.stderr, self._err.append)):
            threading.Thread(target=self._pump, args=(stream, sink), daemon=True).start()
        self._watchdog()

    @staticmethod
    def _pump(stream, sink):
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                chunk = stream.read1(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            sink(dec.decode(chunk))

    def read_stderr(self):
        out, self._err[:] = "".join(self._err), []
        return out

    def _write(self, data):
        try:
            self.proc.stdin.write(data.encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise NDXError("cannot write to the process: %s" % e)

    def close_stdin(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def alive(self):
        return self.proc.poll() is None

    @property
    def exit_code(self):
        return self.proc.poll()

    def pid(self):
        return self.proc.pid

    def _kill(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except (OSError, ProcessLookupError):
            try:
                self.proc.send_signal(sig)
            except OSError:
                pass


# --------------------------------------------------------------------------------------------- backends
class Prepared:
    def __init__(self, argv, env, cwd, note=""):
        self.argv, self.env, self.cwd, self.note = argv, env, cwd, note


class Backend:
    id = "backend"
    title = "Backend"
    supports_pty = True

    def available(self):
        return True, ""

    def prepare(self, argv, cwd, env):
        raise NotImplementedError

    def default_argv(self):
        raise ToolUnavailable("%s has no default interactive shell" % self.title)

    def run(self, argv, cwd=None, env=None, timeout=None, stdin=None, cancel=None, on_output=None,
            merge_stderr=False):
        ok, why = self.available()
        if not ok:
            raise BackendUnavailable("%s is not available: %s" % (self.title, why))
        try:
            p = self.prepare(argv, cwd, env)
        except BackendUnavailable:
            raise
        return run_process(p.argv, cwd=p.cwd, env=p.env, timeout=timeout, stdin_data=stdin, cancel_event=cancel,
                           on_output=on_output, merge_stderr=merge_stderr)

    def open(self, argv, cwd, env, rows, cols, on_update, timeout, scrollback, pty, environment):
        ok, why = self.available()
        if not ok:
            raise BackendUnavailable("%s is not available: %s" % (self.title, why))
        p = self.prepare(argv or self.default_argv(), cwd, env)
        if pty:
            return PtyBridgeSession(environment, self.id, p.argv, p.env, p.cwd, rows, cols, on_update, timeout, scrollback)
        return PipeSession(environment, self.id, p.argv, p.env, p.cwd, timeout)


class NativeProcessBackend(Backend):
    """Executables NDX itself can run: managed toolchain, project envs, authorised external dirs, system."""
    id, title = "host", "NDX host (native processes)"

    def __init__(self, toolchain, shell_env=None):
        self.tc, self.shell_env = toolchain, shell_env

    def default_argv(self):
        prof = self.shell_env.default_profile() if self.shell_env else None
        if prof is None:
            raise ToolUnavailable("no shell found on the host: `ndx tool install bash`")
        return list(prof.argv)

    def prepare(self, argv, cwd, env):
        argv = _check_argv(argv)
        cwd = _check_cwd(cwd)
        full = self.tc.environment("host", project=cwd, extra=_check_env(env)) if self.tc else {**os.environ, **_check_env(env)}
        exe = argv[0]
        if "/" not in exe:
            r = self.tc.resolve_path(exe, project=cwd) if self.tc else None
            if not r:
                raise ToolUnavailable(missing_tool_message(self.tc, exe, "host"))
            exe = r
        return Prepared([exe] + argv[1:], full, cwd)


class AndroidShellBackend(Backend):
    """The Android system shell and system executables, with Android's own (minimal) PATH."""
    id, title = "android", "Android system shell"

    def __init__(self, paths=None):
        self.paths = paths
        self.sh = "/system/bin/sh" if os.path.exists("/system/bin/sh") else "/bin/sh"

    def available(self):
        ok = os.path.exists(self.sh)
        return ok, "" if ok else "no system shell at %s" % self.sh

    def default_argv(self):
        return [self.sh, "-i"] if self.sh.endswith("/sh") else [self.sh]

    def _path_dirs(self):
        return ANDROID_PATH.split(":") if is_android() else ["/usr/bin", "/bin"]

    def prepare(self, argv, cwd, env):
        argv = _check_argv(argv)
        cwd = _check_cwd(cwd)
        base = {"PATH": os.pathsep.join(self._path_dirs()), "TERM": "xterm-256color", "LANG": "C.UTF-8",
                "HOME": str(self.paths.base) if self.paths else os.environ.get("HOME", "/")}
        base.update(_check_env(env))
        exe = argv[0]
        if "/" not in exe:
            for d in self._path_dirs():
                c = os.path.join(d, exe)
                if os.path.isfile(c) and os.access(c, os.X_OK):
                    exe = c
                    break
            else:
                raise ToolUnavailable("%s: not found in the Android system PATH (%s). It may be available in a "
                                      "Linux environment or Termux." % (exe, base["PATH"]))
        return Prepared([exe] + argv[1:], base, cwd)


class PRootBackend(Backend):
    """Run argv inside a bare root filesystem through PRoot (low level; LinuxEnvironmentBackend is the managed one)."""
    id, title = "proot", "PRoot (root filesystem)"

    def __init__(self, toolchain, rootfs=None, extra_env=None):
        self.tc, self.rootfs, self.extra_env = toolchain, rootfs, extra_env or {}

    def for_rootfs(self, rootfs):
        return PRootBackend(self.tc, rootfs, self.extra_env)

    def _proot(self):
        return self.tc.resolve_path("proot") if self.tc else None

    def available(self):
        if not self._proot():
            return False, "proot is not installed (`ndx tool install proot`)"
        if self.rootfs is not None and not os.path.isdir(str(self.rootfs)):
            return False, "root filesystem not found: %s" % self.rootfs
        return True, ""

    def default_argv(self):
        return ["/bin/sh", "-l"]

    def _host_env(self):
        env = dict(os.environ)
        if self.tc:
            env.update({k: v for k, v in self.tc.env.tool_vars().items() if k.startswith("PROOT_")})
        return env

    def prepare(self, argv, cwd, env):
        from ..linux_manager.proot import build_exec_argv
        argv = _check_argv(argv)
        cwd = _check_cwd(cwd)
        full = build_exec_argv(self._proot(), self.rootfs, argv, project_dir=cwd,
                               shell_env={**self.extra_env, **_check_env(env)})
        return Prepared(full, self._host_env(), None, "cwd is bound at /project inside the environment" if cwd else "")


class LinuxEnvironmentBackend(Backend):
    """A managed Linux environment (installed through the Linux Manager)."""
    id, title = "linux", "Linux environment"

    def __init__(self, linux, env_id=None):
        self.linux, self.env_id = linux, env_id

    def for_env(self, env_id):
        return LinuxEnvironmentBackend(self.linux, env_id)

    def available(self):
        if self.linux is None:
            return False, "Linux manager is not available"
        if not self.linux.proot_path():
            return False, "proot is not installed (`ndx tool install proot`)"
        if self.env_id and not self.linux.exists(self.env_id):
            return False, "environment '%s' does not exist" % self.env_id
        return True, ""

    def default_argv(self):
        return None            # login shell, built by prepare()

    def prepare(self, argv, cwd, env):
        cwd = _check_cwd(cwd)
        extra = _check_env(env)
        if argv is None:
            prof = self.linux.profile(self.env_id)
            return Prepared(prof.argv, {**os.environ, **prof.env}, None,
                            "extra variables are not applied to a login shell" if extra else "")
        argv = _check_argv(argv)
        full, _meta = self.linux.exec_argv(self.env_id, argv, project_dir=cwd)
        if extra:
            i = full.index("-i") if "-i" in full else None
            if i is not None:        # /usr/bin/env -i K=V ... : append the caller's variables
                full[i + 1:i + 1] = ["%s=%s" % kv for kv in extra.items()]
        return Prepared(full, {**os.environ, **self.linux._proot_env()}, None,
                        "cwd is bound at /project inside the environment" if cwd else "")


class TermuxBackend(Backend):
    """Commands executed by Termux itself through RUN_COMMAND: non-interactive only, no stdin, no env, no cancel."""
    id, title = "termux", "Termux"
    supports_pty = False

    def __init__(self, termux):
        self.termux = termux

    def available(self):
        if self.termux is None:
            return False, "Termux integration is not available"
        st = self.termux.get_status()          # cheap: never talks to Termux
        if st["state"] == "ready":
            return True, ""
        if st["state"] == "no_response" and st["detail"].startswith("not tested"):
            return True, "not tested yet (checked on first use)"
        return False, "%s: %s" % (st["state"], "; ".join(st["guidance"][:1]))

    def _probe_ready(self):
        st = self.termux.get_status(probe=True)          # one harmless command, cached for 60 s
        if st["state"] == "ready":
            return True, ""
        return False, "%s: %s" % (st["state"], "; ".join(st["guidance"][:1]))

    def open(self, *a, **k):
        raise BackendUnavailable("interactive sessions are not possible through Termux RUN_COMMAND "
                                 "(commands run in the background without a terminal); use run()")

    def run(self, argv, cwd=None, env=None, timeout=None, stdin=None, cancel=None, on_output=None, merge_stderr=False):
        ok, why = self.available()
        if ok:
            ok, why = self._probe_ready()
        if not ok:
            raise BackendUnavailable("Termux is not available: %s" % why)
        argv = _check_argv(argv)
        if env:
            raise ValidationError("environment variables cannot be passed to Termux commands (RUN_COMMAND has no env)")
        if stdin is not None:
            raise ValidationError("stdin cannot be passed to Termux commands")
        res = CommandResult(argv=list(argv))
        t0, box = time.time(), {}

        def work():
            try:
                box["r"] = self.termux.run(argv[0], argv[1:], cwd=cwd, timeout=timeout or 60)
            except NDXError as e:
                box["e"] = e

        th = threading.Thread(target=work, daemon=True)
        th.start()
        while th.is_alive():
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                res.error = "cancelled (the command may still be running inside Termux)"
                res.duration = time.time() - t0
                return res
            th.join(0.05)
        res.duration = time.time() - t0
        if "e" in box:
            res.error, res.returncode = str(box["e"]), 126
            return res
        r = box["r"]
        res.stdout, res.stderr, res.returncode, res.timed_out = r.stdout, r.stderr, r.exit_code, r.timed_out
        if r.timed_out:
            res.error = "timed out waiting for Termux"
        elif r.exit_code is None:
            res.error = r.errmsg or "Termux did not report an exit code"
            res.returncode = 126
        if on_output:
            for name, txt in (("stdout", r.stdout), ("stderr", r.stderr)):
                if txt:
                    on_output(name, txt)
        return res


def missing_tool_message(tc, command, runtime):
    msg = "%s: command not found in the %s environment." % (command, runtime)
    m = tc.registry.by_command(command) if tc is not None else None
    if m:
        msg += " NDX can install it: `ndx tool install %s`" % m.id
    return msg


# --------------------------------------------------------------------------------------------- bridge
class TerminalBridge(Module):
    name = "bridge"
    title = "Terminal Bridge"

    def __init__(self, paths, toolchain, linux=None, termux=None, shell_env=None, resources=None, audit=None, bus=None,
                 config=None):
        self.paths, self.tc, self.linux, self.termux = paths, toolchain, linux, termux
        self.resources, self.bus, self.config = resources, bus, config
        self.audit = audit or (lambda *a, **k: None)
        self.native = NativeProcessBackend(toolchain, shell_env)
        self.android = AndroidShellBackend(paths)
        self.proot = PRootBackend(toolchain)
        self.linux_backend = LinuxEnvironmentBackend(linux)
        self.termux_backend = TermuxBackend(termux)
        self.sessions = {}
        self._lock = threading.Lock()
        self.script_env = None        # fn(label, ttl) -> (env_dict, revoke_fn): lets scripts call back into NDX (IPC)

    def stop(self):
        self.terminate_all()
        self.status = "stopped"

    # ---- environments
    def environments(self):
        rows = []
        for env, b in (("host", self.native), ("android", self.android), ("termux", self.termux_backend)):
            ok, why = b.available()
            rows.append({"id": env, "backend": b.id, "available": ok, "reason": why, "pty": b.supports_pty})
        if self.linux is not None:
            for e in self.linux.list():
                ok, why = self.linux_backend.for_env(e["id"]).available()
                rows.append({"id": e["id"], "backend": "linux", "available": ok, "reason": why, "pty": True})
        return rows

    def backend_for(self, environment):
        env = environment or "host"
        if env == "host":
            return self.native
        if env == "android":
            return self.android
        if env == "termux":
            return self.termux_backend
        if env.startswith("proot:"):
            root = env[len("proot:"):]
            if not os.path.isabs(root):
                raise ValidationError("proot:<path> needs an absolute root filesystem path")
            return self.proot.for_rootfs(root)
        eid = env[len("linux:"):] if env.startswith("linux:") else env
        if self.linux is not None:
            from ..linux_manager import validate_env_id
            validate_env_id(eid)
            if self.linux.exists(eid):
                return self.linux_backend.for_env(eid)
        raise ValidationError("unknown environment '%s'. Available: %s" %
                              (env, ", ".join(r["id"] for r in self.environments())))

    # ---- one-shot commands
    def run(self, argv, environment="host", cwd=None, env=None, timeout=None, stdin=None, cancel=None, on_output=None,
            caller="core", merge_stderr=False, script=False):
        argv = _check_argv(argv)
        backend = self.backend_for(environment)
        revoke = None
        if script and self.script_env:
            extra, revoke = self.script_env("run:" + os.path.basename(argv[0]), (timeout or 600) + 30)
            env = {**extra, **(env or {})} if backend.id != "termux" else env
        self.audit("terminal.run", environment=environment, command=os.path.basename(argv[0]), argc=len(argv),
                   caller=caller)
        op = self.resources.track("process", os.path.basename(argv[0])) if self.resources else None
        if self.resources is not None:
            adv = self.resources.admit("process")
            if adv["warnings"] and self.bus:
                self.bus.publish("resources.warning", {"warnings": adv["warnings"], "what": argv[0]}, "bridge")
        try:
            if op:
                with op:
                    res = backend.run(argv, cwd, env, timeout, stdin, cancel, on_output, merge_stderr)
            else:
                res = backend.run(argv, cwd, env, timeout, stdin, cancel, on_output, merge_stderr)
        except ToolUnavailable as e:
            res = CommandResult(argv=list(argv), returncode=127, error=str(e))
        finally:
            if revoke:
                revoke()
        if res.returncode == 127 and "not found" in (res.error or res.stderr) and "ndx tool install" not in (res.error + res.stderr):
            res.error = (res.error or res.stderr.strip()) + "\n" + missing_tool_message(self.tc, argv[0], environment)
        return res

    # ---- interactive sessions
    def open(self, environment="host", argv=None, cwd=None, env=None, rows=24, cols=80, on_update=None, timeout=None,
             pty=True, caller="core"):
        backend = self.backend_for(environment)
        if argv is not None:
            argv = _check_argv(argv)
        scroll = self.config.get("terminal.scrollback", 2000) if self.config else 2000
        self.audit("terminal.open", environment=environment, caller=caller, pty=pty)
        s = backend.open(argv, cwd, _check_env(env), rows, cols, on_update, timeout, scroll, pty, environment)
        s._on_close = lambda sess: self.sessions.pop(sess.id, None)
        with self._lock:
            self.sessions[s.id] = s
        return s

    def session(self, sid):
        return self.sessions.get(sid)

    def list_sessions(self):
        return [s.status() for s in list(self.sessions.values())]

    def terminate_all(self):
        for s in list(self.sessions.values()):
            try:
                s.terminate(0.5)
            except Exception:
                pass
        self.sessions.clear()
