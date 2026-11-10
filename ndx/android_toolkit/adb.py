"""ADB integration through the official `adb` client (not bundled). Wireless
debugging uses Android's own pairing flow (`adb pair host:port CODE`)."""
import collections
import re
import subprocess
import threading
import time

from ..core.errors import ToolUnavailable
from ..utilities import run_capture
from ..toolchain_manager.manager import host_which as _hw


def which(cmd, extra_dirs=()):
    return _hw(cmd, extra_dirs)


HOSTPORT_RE = re.compile(r"^[\w.\-]+:\d{2,5}$")
MIN_ADB = (1, 0, 41)          # wireless debugging (pair/connect over TLS) needs platform-tools 30+ (adb 1.0.41)

# connection states (kept distinct on purpose: they need different user actions)
ADB_UNAVAILABLE, ADB_AVAILABLE = "adb_unavailable", "adb_available"
PAIRING_REQUIRED, AUTH_REQUIRED, DISCONNECTED, CONNECTED = "pairing_required", "authorization_required", "device_disconnected", "connected"


def _ver(text):
    m = re.search(r"version (\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


class AdbClient:
    """The official `adb` client, resolved through the Toolchain Manager (never assumed to exist on Android).

    `environment` selects where adb runs: "host" (a managed/system adb NDX can execute) or the name of a Linux
    environment / "termux" (adb installed there, run through the Terminal Bridge). NDX never bypasses Android's
    authorization: pairing codes and the "Allow USB/wireless debugging" prompt are always the user's."""

    def __init__(self, extra_dirs=(), toolchain=None, bridge=None, environment="host"):
        self.extra_dirs = extra_dirs
        self.toolchain, self.bridge, self.environment = toolchain, bridge, environment

    # ---- availability
    def path(self):
        """Host adb path (None when adb is not runnable by NDX itself)."""
        if self.toolchain is not None:
            return self.toolchain.resolve_path("adb")
        return which("adb", self.extra_dirs)

    def available(self):
        if self.environment != "host" and self.bridge is not None:
            return self.bridge.run(["adb", "version"], self.environment, timeout=15).ok
        return self.path() is not None

    def install_hint(self):
        return ("adb is not installed. Install it: `ndx tool install adb` (NDX installs it inside a Linux environment "
                "or Termux where Android has no native build) and set the adb environment in Settings.")

    def ensure(self, install=False, confirm=True):
        if self.available():
            return True
        if install and self.toolchain is not None:
            self.toolchain.install("adb", confirm=confirm)
            return self.available()
        raise ToolUnavailable(self.install_hint())

    def _run(self, *args, timeout=20):
        if self.environment != "host" and self.bridge is not None:
            r = self.bridge.run(["adb", *args], self.environment, timeout=timeout)
            if r.returncode == 127:
                raise ToolUnavailable(r.error or self.install_hint())
            return (r.returncode if r.returncode is not None else 1), r.stdout, r.stderr or r.error
        p = self.path()
        if not p:
            raise ToolUnavailable(self.install_hint())
        env = self.toolchain.environment("host") if self.toolchain is not None else None
        return run_capture([p, *args], timeout=timeout, env=env)

    def version(self):
        """-> (major, minor, patch) or None."""
        rc, out, _e = self._run("version", timeout=10)
        return _ver(out) if rc == 0 else None

    def version_ok(self):
        v = self.version()
        return v is not None and v >= MIN_ADB

    # ---- devices / states
    def devices(self):
        rc, out, err = self._run("devices", "-l")
        devs = []
        for line in out.splitlines():
            if line.startswith(("List of", "*")) or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                state = parts[1]
                devs.append({"serial": parts[0], "state": state, "info": " ".join(parts[2:]),
                             "status": {"device": CONNECTED, "unauthorized": AUTH_REQUIRED, "offline": DISCONNECTED,
                                        "no": AUTH_REQUIRED}.get(state, state)})
        return devs

    def status(self):
        """Overall, honest state: adb_unavailable | device_disconnected | authorization_required | connected."""
        if not self.available():
            return {"state": ADB_UNAVAILABLE, "message": self.install_hint(), "devices": []}
        devs = self.devices()
        if not devs:
            return {"state": DISCONNECTED, "message": "adb works but no device is connected. Pair/connect over "
                    "Wireless debugging, or plug in USB.", "devices": []}
        if any(d["status"] == CONNECTED for d in devs):
            return {"state": CONNECTED, "message": "%d device(s) connected" % sum(d["status"] == CONNECTED for d in devs),
                    "devices": devs}
        if any(d["status"] == AUTH_REQUIRED for d in devs):
            return {"state": AUTH_REQUIRED, "message": "Accept the 'Allow debugging' prompt on the device.", "devices": devs}
        return {"state": DISCONNECTED, "message": "device is offline; reconnect it", "devices": devs}

    def discover(self, timeout=10):
        """Wireless-debugging services advertised on the network (`adb mdns services`) -> [{name, kind, address}]."""
        rc, out, err = self._run("mdns", "services", timeout=timeout)
        found = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1].startswith("_adb"):
                found.append({"name": parts[0], "kind": "pairing" if "pairing" in parts[1] else "connect",
                              "address": parts[2]})
        return found

    def pair(self, host_port, code):
        """host_port/code are shown by Settings > Developer options > Wireless debugging."""
        if not HOSTPORT_RE.match(host_port or "") or not re.match(r"^\d{4,8}$", code or ""):
            raise ValueError("invalid pairing address or code")
        v = self.version()
        if v is not None and v < MIN_ADB:
            return False, "this adb is too old for wireless pairing (%s, needs >= %s)" % (".".join(map(str, v)), ".".join(map(str, MIN_ADB)))
        rc, out, err = self._run("pair", host_port, code)
        return rc == 0 and "successfully" in (out + err).lower(), (out + err).strip()

    def connect_status(self, host_port):
        """-> {state, message}: connected | pairing_required | authorization_required | device_disconnected."""
        if not HOSTPORT_RE.match(host_port or ""):
            raise ValueError("invalid host:port")
        rc, out, err = self._run("connect", host_port, timeout=25)
        text = (out + err).strip()
        low = text.lower()
        if "connected to" in low and "failed" not in low or "already connected" in low:
            return {"state": CONNECTED, "message": text}
        if "failed to authenticate" in low or "protocol fault" in low or "unauthorized" in low:
            return {"state": PAIRING_REQUIRED, "message": text + "\nPair this device first (Wireless debugging > Pair "
                    "device with pairing code)."}
        if "not authorized" in low or "authorize" in low:
            return {"state": AUTH_REQUIRED, "message": text}
        return {"state": DISCONNECTED, "message": text or "no answer"}

    def connect(self, host_port):
        r = self.connect_status(host_port)
        return r["state"] == CONNECTED, r["message"]

    def disconnect(self, host_port=""):
        if host_port and not HOSTPORT_RE.match(host_port):
            raise ValueError("invalid host:port")
        return self._run("disconnect", *([host_port] if host_port else []))[1].strip()

    def shell(self, serial, command, timeout=30):
        rc, out, err = self._run("-s", serial, "shell", command, timeout=timeout)
        return rc, out, err

    def diagnostics(self):
        """Detailed checks for the Device Toolkit screen -> [{name, status ok|warn|fail, detail}]."""
        out = []
        if not self.available():
            return [{"name": "adb executable", "status": "fail", "detail": self.install_hint()}]
        out.append({"name": "adb executable", "status": "ok",
                    "detail": self.path() or "in environment '%s'" % self.environment})
        v = self.version()
        out.append({"name": "adb version", "status": "ok" if v and v >= MIN_ADB else "warn",
                    "detail": ".".join(map(str, v)) if v else "unknown (wireless pairing needs >= 1.0.41)"})
        st = self.status()
        out.append({"name": "connection", "status": "ok" if st["state"] == CONNECTED else "warn", "detail": st["message"]})
        return out

class CrashLogStore:
    MARK = re.compile(r"FATAL EXCEPTION|ANR in |Fatal signal")

    def __init__(self, directory):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_if_crash(self, lines):
        text = "\n".join(lines)
        if self.MARK.search(text):
            f = self.dir / ("crash_%d.log" % int(time.time()))
            f.write_text(text, encoding="utf-8")
            return f
        return None

    def list(self):
        return sorted(self.dir.glob("crash_*.log"), reverse=True)


class LogcatMonitor:
    LEVELS = "VDIWEF"

    def __init__(self, adb, crash_store=None, capacity=3000):
        self.adb, self.crash = adb, crash_store
        self.lines = collections.deque(maxlen=capacity)
        self.proc = None
        self.on_line = lambda l: None

    def start(self, serial=None, min_level="I"):
        if self.proc:
            return
        base = [self.adb.path() or ""]
        if not base[0]:
            raise ToolUnavailable("adb not found")
        argv = base + (["-s", serial] if serial else []) + ["logcat", "-v", "threadtime", "*:" + min_level]
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        window = collections.deque(maxlen=60)
        for line in self.proc.stdout:
            line = line.rstrip()
            self.lines.append(line)
            window.append(line)
            if self.crash and "FATAL EXCEPTION" in line:
                threading.Timer(1.0, lambda w=list(window): self.crash.save_if_crash(w)).start()
            self.on_line(line)
        self.proc = None

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def filter(self, text="", level="V", regex=False):
        lv = self.LEVELS.index(level) if level in self.LEVELS else 0
        out = []
        for l in self.lines:
            m = re.search(r"\s([VDIWEF])\s", l)
            if m and self.LEVELS.index(m.group(1)) < lv:
                continue
            if text and not ((re.search(text, l) is not None) if regex else (text.lower() in l.lower())):
                continue
            out.append(l)
        return out
