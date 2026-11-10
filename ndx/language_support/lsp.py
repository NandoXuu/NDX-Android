"""Minimal LSP client over stdio (JSON-RPC with Content-Length framing).
It starts a language server when one is installed; nothing is faked when
it is not (start() returns False)."""
import json
import subprocess
import threading

from ..toolchain_manager.manager import host_which as which


class LspClient:
    def __init__(self, argv, root_uri, on_notification=None):
        self.argv = argv
        self.root_uri = root_uri
        self.on_notification = on_notification or (lambda m: None)
        self.proc = None
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()

    def start(self):
        if not which(self.argv[0]):
            return False
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        threading.Thread(target=self._reader, daemon=True).start()
        res = self.request("initialize", {"processId": None, "rootUri": self.root_uri, "capabilities": {}},
                           timeout=15)
        if res is None:
            self.stop()
            return False
        self.notify("initialized", {})
        return True

    def _send(self, msg):
        body = json.dumps(msg).encode("utf-8")
        with self._lock:
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.proc.stdin.flush()

    def request(self, method, params, timeout=10):
        self._id += 1
        rid, ev = self._id, threading.Event()
        self._pending[rid] = [ev, None]
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        ev.wait(timeout)
        return self._pending.pop(rid, [None, None])[1]

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _reader(self):
        out = self.proc.stdout
        while True:
            headers = {}
            while True:
                line = out.readline()
                if not line:
                    return
                line = line.strip()
                if not line:
                    break
                k, _, v = line.decode("ascii", "replace").partition(":")
                headers[k.lower()] = v.strip()
            n = int(headers.get("content-length", "0"))
            msg = json.loads(out.read(n).decode("utf-8")) if n else {}
            if "id" in msg and msg["id"] in self._pending and "method" not in msg:
                self._pending[msg["id"]][1] = msg.get("result", {})
                self._pending[msg["id"]][0].set()
            else:
                self.on_notification(msg)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
