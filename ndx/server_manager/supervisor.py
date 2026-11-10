"""Background supervisor (spec §13).

Two halves that talk ONLY through files in the app's private data directory (no sockets, no new permissions):

  Supervisor      runs in the Android foreground-service process (service/main.py). It owns external-process servers
                  delegated to it, restarts nothing by itself unless the spec says so, and writes `heartbeat.json`
                  and `state.json` every tick.
  BackgroundLink  used by the UI process / `ndx service`. Delegates start/stop commands and reports an HONEST status.

Honesty rules (docs/BACKGROUND.md):
  * A foreground service lowers the chance Android kills NDX; it never guarantees it (battery optimisation, OEM task
    killers, user swipe, memory pressure).  Status text says "running", "stale" or "not running", never "guaranteed".
  * Only servers already saved (and validated) in servers.json can be delegated: the inbox carries an id, never a
    command line.  Built-in static servers live in the UI process and cannot be delegated.
  * A delegated server stays running only while the service process does."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

HEARTBEAT_STALE_S = 30
NOTIFICATION_TEXT = "NDX is running %d background server(s)"
NOTIFICATION_IDLE = "NDX background service is idle"


def _atomic(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


class Supervisor:
    def __init__(self, data_dir, clock=time.time, spawn=None):
        self.dir = Path(data_dir)
        self.svc = self.dir / "service"
        self.inbox = self.svc / "inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.spawn = spawn or self._spawn
        self.procs = {}        # sid -> Popen
        self.info = {}         # sid -> dict(state, pid, started, exit, error)

    def specs(self):
        return {d.get("id"): d for d in _read(self.dir / "servers.json", []) if isinstance(d, dict)}

    @staticmethod
    def _spawn(spec):
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        env.update({"HOST": str(spec.get("host", "127.0.0.1")), "PORT": str(spec.get("port", 8080))})
        return subprocess.Popen(list(spec["command"]), cwd=spec.get("cwd") or None, env=env, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start_server(self, sid):
        spec = self.specs().get(sid)
        if spec is None:
            self.info[sid] = {"state": "error", "error": "unknown server id"}
        elif spec.get("runtime") == "builtin-static" or not spec.get("command"):
            self.info[sid] = {"state": "error", "error": "only external-process servers can run in the service"}
        elif sid in self.procs and self.procs[sid].poll() is None:
            pass
        else:
            try:
                p = self.spawn(spec)
                self.procs[sid] = p
                self.info[sid] = {"state": "running", "pid": p.pid, "started": self.clock()}
            except (OSError, ValueError) as e:
                self.info[sid] = {"state": "error", "error": str(e)}

    def stop_server(self, sid, grace=3.0):
        p = self.procs.pop(sid, None)
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (OSError, AttributeError):
                p.terminate()
            try:
                p.wait(grace)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (OSError, AttributeError):
                    p.kill()
                p.wait(2)
        if sid in self.info:
            self.info[sid] = {"state": "stopped", "exit": p.returncode if p else None}

    def tick(self):
        """One supervision step: apply inbox commands, reap exits, write state + heartbeat. Returns the state."""
        for f in sorted(self.inbox.glob("*.json")):
            cmd = _read(f, {})
            try:
                f.unlink()
            except OSError:
                pass
            op, sid = cmd.get("op"), cmd.get("id")
            if op == "start" and isinstance(sid, str):
                self.start_server(sid)
            elif op == "stop" and isinstance(sid, str):
                self.stop_server(sid)
            elif op == "stop_all":
                for s in list(self.procs):
                    self.stop_server(s)
        for sid, p in list(self.procs.items()):
            code = p.poll()
            if code is not None:
                self.procs.pop(sid)
                self.info[sid] = {"state": "exited", "exit": code, "error": "" if code == 0 else "exited with code %s" % code}
        running = [s for s, p in self.procs.items() if p.poll() is None]
        state = {"time": self.clock(), "pid": os.getpid(), "servers": self.info,
                 "notification": (NOTIFICATION_TEXT % len(running)) if running else NOTIFICATION_IDLE}
        _atomic(self.svc / "state.json", state)
        _atomic(self.svc / "heartbeat.json", {"time": self.clock(), "pid": os.getpid()})
        return state

    def run_forever(self, interval=2.0, stop=lambda: False):
        try:
            while not stop():
                self.tick()
                time.sleep(interval)
        finally:
            for s in list(self.procs):
                self.stop_server(s)
            _atomic(self.svc / "heartbeat.json", {"time": 0, "pid": os.getpid(), "stopped": True})


class BackgroundLink:
    def __init__(self, data_dir, clock=time.time):
        self.dir = Path(data_dir)
        self.svc = self.dir / "service"
        self.clock = clock
        self._seq = 0

    def _send(self, cmd):
        self.svc.joinpath("inbox").mkdir(parents=True, exist_ok=True)
        self._seq += 1
        name = "%d-%d-%d.json" % (self.clock() * 1000, os.getpid(), self._seq)
        _atomic(self.svc / "inbox" / name, cmd)

    def delegate(self, sid):
        """Ask the service to run saved external-process server `sid`. Refuses when the service is not alive."""
        if not self.alive():
            raise RuntimeError("the background service is not running (no recent heartbeat); the server was not delegated")
        self._send({"op": "start", "id": sid})

    def release(self, sid):
        self._send({"op": "stop", "id": sid})

    def heartbeat_age(self):
        hb = _read(self.svc / "heartbeat.json", None)
        if not hb or hb.get("stopped") or not hb.get("time"):
            return None
        return max(0.0, self.clock() - float(hb["time"]))

    def alive(self):
        age = self.heartbeat_age()
        return age is not None and age <= HEARTBEAT_STALE_S

    def status(self):
        age = self.heartbeat_age()
        st = _read(self.svc / "state.json", {})
        if age is None:
            return {"summary": "not running", "alive": False,
                    "details": ["no service heartbeat: servers run only while the NDX app process is alive and Android may stop it at any time",
                                "the foreground service starts with the app when the build includes it (see docs/BACKGROUND.md)"]}
        if age > HEARTBEAT_STALE_S:
            return {"summary": "stale (last heartbeat %ds ago)" % age, "alive": False,
                    "details": ["the service may have been killed by Android (battery optimisation / task killer / memory)"]}
        det = ["service pid %s, heartbeat %ds ago" % (st.get("pid"), age), st.get("notification", "")]
        for sid, i in (st.get("servers") or {}).items():
            det.append("%s: %s%s" % (sid, i.get("state"), " (%s)" % i["error"] if i.get("error") else ""))
        det.append("this reduces, but does not remove, the chance that Android stops NDX")
        return {"summary": "running", "alive": True, "details": det}
