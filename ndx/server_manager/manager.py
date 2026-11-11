"""Local server manager. Servers are external processes (python/node/rust/C...)
or the built-in static HTTP server. Default bind is loopback only."""
import collections
import functools
import http.server
import json
import os
import signal
import socketserver
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict

from ..core.errors import NDXError, ValidationError
from ..core.registry import Module
from ..resource_manager.budget import RequestGate

LOOPBACK = ("127.0.0.1", "::1", "localhost")
KINDS = ["http", "https", "websocket", "rest", "graphql", "database", "file", "ai", "chatbot", "tcp", "udp", "game", "custom"]
POLICIES = ["throttle", "reject", "shutdown"]


@dataclass
class ServerSpec:
    id: str
    name: str
    kind: str = "http"
    runtime: str = "builtin-static"       # builtin-static | python | node | rust | c | custom
    command: list = field(default_factory=list)
    cwd: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    env: dict = field(default_factory=dict)
    allow_external: bool = False
    ram_mb: int = 256                     # RAM budget (reservation and monitoring; not a hard OS limit)
    threads: int = 2                      # advisory (OMP/…_NUM_THREADS)
    nice: int = 0
    max_concurrency: int = 8
    queue_limit: int = 16
    timeout_s: float = 10.0
    policy: str = "reject"                # action when RAM budget is exceeded
    autostart: bool = False


class _Handler(http.server.SimpleHTTPRequestHandler):
    gate = None
    log_fn = None

    def do_GET(self):
        if not self.gate.acquire():
            self.send_error(503, "Server busy (resource limits)")
            return
        try:
            super().do_GET()
        finally:
            self.gate.release()

    def log_message(self, fmt, *args):
        if self.log_fn:
            self.log_fn("%s - %s" % (self.address_string(), fmt % args))


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class ServerRuntime:
    def __init__(self, spec):
        self.spec = spec
        self.state = "stopped"      # stopped starting running failed
        self.proc = None
        self.httpd = None
        self.gate = RequestGate(spec.max_concurrency, spec.queue_limit, spec.timeout_s)
        self.logs = collections.deque(maxlen=500)
        self.started = None
        self.error = ""
        self.throttled = False

    def log(self, line):
        self.logs.append("%s %s" % (time.strftime("%H:%M:%S"), line.rstrip()))


class ServerManager(Module):
    name = "servers"
    title = "Server Manager"

    def __init__(self, data_dir, bus, resources, monitor, audit=None, consent=None):
        self.file = data_dir / "servers.json"
        self.bus, self.resources, self.monitor, self.consent = bus, resources, monitor, consent
        self.audit = audit or (lambda *a, **k: None)
        self.runtimes = {}
        from .supervisor import BackgroundLink
        self.background = BackgroundLink(data_dir)
        self._watch = None
        self._stop = threading.Event()

    # ---- specs
    def start(self, core):
        try:
            for d in json.loads(self.file.read_text()):
                spec = ServerSpec(**{k: v for k, v in d.items() if k in ServerSpec.__dataclass_fields__})
                self.runtimes[spec.id] = ServerRuntime(spec)
        except (OSError, ValueError, TypeError):
            pass
        self._watch = threading.Thread(target=self._watcher, daemon=True, name="ndx-server-watch")
        self._watch.start()

    def _persist(self):
        tmp = str(self.file) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump([asdict(r.spec) for r in self.runtimes.values()], fh, indent=2)
        os.replace(tmp, self.file)

    def validate(self, spec):
        if spec.kind not in KINDS:
            raise ValidationError("unknown server kind")
        if spec.policy not in POLICIES:
            raise ValidationError("unknown policy")
        if not (1 <= int(spec.port) <= 65535):
            raise ValidationError("invalid port")
        if spec.host not in LOOPBACK and not spec.allow_external:
            raise ValidationError("Binding to %s exposes the server to the network. Enable "
                                  "'allow external' explicitly to do that." % spec.host)
        if spec.runtime != "builtin-static" and not spec.command:
            raise ValidationError("a command is required for runtime '%s'" % spec.runtime)

    def add(self, spec):
        self.validate(spec)
        if spec.id in self.runtimes:
            raise ValidationError("server id already exists")
        self.runtimes[spec.id] = ServerRuntime(spec)
        self._persist()
        return spec

    def update(self, spec):
        self.validate(spec)
        rt = self.runtimes[spec.id]
        if rt.state == "running":
            raise ValidationError("stop the server before changing its configuration")
        rt.spec = spec
        rt.gate = RequestGate(spec.max_concurrency, spec.queue_limit, spec.timeout_s)
        self._persist()

    def remove(self, sid):
        rt = self.runtimes.get(sid)
        if rt and rt.state == "running":
            self.stop_server(sid)
        self.runtimes.pop(sid, None)
        self._persist()

    # ---- lifecycle
    def start_server(self, sid):
        rt = self.runtimes[sid]
        spec = rt.spec
        if rt.state == "running":
            return
        self.validate(spec)
        if hasattr(self.resources, "admit"):            # advisory: concurrent heavy tasks / low free RAM
            for w in self.resources.admit("server", spec.ram_mb)["warnings"]:
                rt.log("warning: " + w)
                self.bus.publish("server.warning", {"id": sid, "message": w}, "servers")
        if not self.resources.reserve("server:" + sid, spec.ram_mb):
            rt.error = "RAM budget exceeded: cannot reserve %d MB" % spec.ram_mb
            rt.state = "failed"
            raise NDXError(rt.error)
        rt.state, rt.error, rt.throttled = "starting", "", False
        try:
            if spec.runtime == "builtin-static":
                self._start_static(rt)
            else:
                self._start_process(rt)
        except Exception as exc:
            rt.state, rt.error = "failed", str(exc)
            self.resources.release("server:" + sid)
            rt.log("failed to start: %s" % exc)
            raise
        rt.started, rt.state = time.time(), "running"
        self.audit("server.start", id=sid, host=spec.host, port=spec.port, runtime=spec.runtime)
        self.bus.publish("server.changed", {"id": sid, "state": "running"}, "servers")

    def _start_static(self, rt):
        spec = rt.spec
        root = spec.cwd or os.getcwd()
        if not os.path.isdir(root):
            raise NDXError("directory does not exist: %s" % root)
        handler = type("H", (_Handler,), {"gate": rt.gate, "log_fn": rt.log})
        httpd = _ThreadingServer((spec.host, int(spec.port)), functools.partial(handler, directory=root))
        rt.httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True, name="ndx-http-" + spec.id).start()
        rt.log("serving %s on http://%s:%d" % (root, spec.host, spec.port))

    def _start_process(self, rt):
        spec = rt.spec
        env = dict(os.environ)
        env.update(spec.env)
        env.update({"HOST": spec.host, "PORT": str(spec.port), "OMP_NUM_THREADS": str(spec.threads),
                    "RAYON_NUM_THREADS": str(spec.threads), "GOMAXPROCS": str(spec.threads)})

        rt.proc = subprocess.Popen(
            spec.command,
            cwd=spec.cwd or None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        threading.Thread(target=self._pump, args=(rt, rt.proc), daemon=True).start()

    def _pump(self, rt, proc):
        try:
            if proc.stdout is not None:
                with proc.stdout:
                    for line in proc.stdout:
                        rt.log(line)
            code = proc.wait()
        finally:
            if proc.stdout is not None and not proc.stdout.closed:
                proc.stdout.close()

        if rt.state == "running":
            rt.state = "stopped" if code == 0 else "failed"
            rt.error = "" if code == 0 else "exited with code %s" % code
            self.resources.release("server:" + rt.spec.id)
            self.bus.publish("server.changed", {"id": rt.spec.id, "state": rt.state}, "servers")

    def stop_server(self, sid):
        rt = self.runtimes[sid]
        was = rt.state
        rt.state = "stopped"
        if rt.httpd:
            rt.httpd.shutdown()
            rt.httpd.server_close()
            rt.httpd = None
        if rt.proc and rt.proc.poll() is None:
            try:
                os.killpg(rt.proc.pid, signal.SIGTERM)
                rt.proc.wait(5)
            except (subprocess.TimeoutExpired, OSError, ProcessLookupError):
                try:
                    os.killpg(rt.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        rt.proc = None
        rt.started = None
        self.resources.release("server:" + sid)
        if was != "stopped":
            self.audit("server.stop", id=sid)
            self.bus.publish("server.changed", {"id": sid, "state": "stopped"}, "servers")

    def restart(self, sid):
        self.stop_server(sid)
        self.start_server(sid)

    # ---- stats / policy
    def stats(self, sid):
        rt = self.runtimes[sid]
        s = {"state": rt.state, "uptime": (time.time() - rt.started) if rt.started else None,
             "cpu": None, "rss": None, "requests": None, "rejected": None, "throttled": rt.throttled,
             "error": rt.error}
        if rt.httpd or rt.spec.runtime == "builtin-static":
            g = rt.gate.stats()
            s["requests"], s["rejected"] = g["served"], g["rejected"]
            if rt.state == "running":
                p = self.monitor.process(os.getpid())
                s["rss"] = p["rss"]["value"] if p else None   # whole NDX process (shared)
                s["rss_scope"] = "NDX app process (shared)"
        elif rt.proc and rt.proc.poll() is None:
            p = self.monitor.process(rt.proc.pid)
            if p:
                s["cpu"], s["rss"] = p["cpu"]["value"], p["rss"]["value"]
        return s

    def _watcher(self):
        while not self._stop.wait(2.0):
            for sid, rt in list(self.runtimes.items()):
                if rt.state != "running" or not rt.proc:
                    continue
                p = self.monitor.process(rt.proc.pid)
                if not p or p["rss"]["value"] is None:
                    continue
                over = p["rss"]["value"] > rt.spec.ram_mb * 1024 * 1024
                if over and rt.spec.policy == "shutdown":
                    rt.log("RAM budget exceeded; shutting down (policy=shutdown)")
                    self.stop_server(sid)
                elif over and rt.spec.policy == "throttle" and not rt.throttled:
                    rt.throttled = True
                    try:
                        os.killpg(rt.proc.pid, signal.SIGSTOP)
                        threading.Timer(3.0, self._resume, args=(sid,)).start()
                        rt.log("RAM budget exceeded; paused 3s (policy=throttle)")
                    except OSError:
                        pass
                elif over and rt.spec.policy == "reject":
                    rt.log("RAM budget exceeded (policy=reject: nothing enforced for external processes)")

    def _resume(self, sid):
        rt = self.runtimes.get(sid)
        if rt and rt.proc and rt.proc.poll() is None:
            try:
                os.killpg(rt.proc.pid, signal.SIGCONT)
            except OSError:
                pass
            rt.throttled = False

    def running(self):
        return [r for r in self.runtimes.values() if r.state == "running"]

    def stop_all(self):
        for sid in list(self.runtimes):
            if self.runtimes[sid].state == "running":
                self.stop_server(sid)

    def stop(self):
        self._stop.set()
        self.stop_all()
