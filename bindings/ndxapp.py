"""Python client for the NDX IPC (stdlib only). Authenticates first with a session token.

    app = NdxApp()                       # port/token from NDX_IPC_PORT / NDX_IPC_TOKEN (set by NDX for its scripts)
    r = app.tools_run("git", ["status"], environment="host")
"""
import json
import os
import socket


class NdxError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class NdxApp:
    def __init__(self, port=None, host="127.0.0.1", token=None, timeout=10):
        port = port if port is not None else os.environ.get("NDX_IPC_PORT")
        token = token if token is not None else os.environ.get("NDX_IPC_TOKEN")
        if not port:
            raise NdxError("no IPC port: pass port= or set NDX_IPC_PORT", "no_port")
        if not token:
            raise NdxError("no IPC token: pass token= or set NDX_IPC_TOKEN", "no_token")
        self.timeout = timeout
        self._s = socket.create_connection((host, int(port)), timeout=timeout)
        self._s.settimeout(timeout)
        self._f = self._s.makefile("rwb")
        self._id = 0
        self.scopes = self._call("auth", token=token).get("scopes", [])

    def _call(self, method, _timeout=None, **params):
        self._id += 1
        if _timeout is not None:
            self._s.settimeout(_timeout)
        try:
            self._f.write((json.dumps({"id": self._id, "method": method, "params": params}) + "\n").encode())
            self._f.flush()
            while True:
                line = self._f.readline()
                if not line:
                    raise NdxError("connection closed by NDX", "closed")
                msg = json.loads(line)
                if "event" in msg:
                    continue
                if "error" in msg:
                    raise NdxError(msg["error"] if isinstance(msg["error"], str) else str(msg["error"]),
                                   msg.get("code"))
                return msg["result"]
        except socket.timeout:
            raise NdxError("timed out waiting for NDX", "timeout")
        finally:
            if _timeout is not None:
                self._s.settimeout(self.timeout)

    # ---- UI (scope: ui)
    def window(self, title):
        return self._call("window.create", title=title)

    def add(self, parent, kind, **props):
        return self._call("node.add", parent=parent, kind=kind, props=props)

    def set(self, node, **props):
        return self._call("node.set", node=node, props=props)

    def tree(self):
        return self._call("ui.tree")

    # ---- key/value (scope: kv)
    def kv_get(self, key):
        return self._call("services.kv_get", key=key)

    def kv_set(self, key, value):
        return self._call("services.kv_set", key=key, value=value)

    # ---- tools (scope: tools). Arguments are a list: never build a shell string from untrusted input.
    def tools_run(self, tool, args=(), environment="host", cwd=None, env=None, timeout=None, input=None):
        params = {"tool": tool, "args": list(args), "environment": environment}
        for k, v in (("cwd", cwd), ("env", env), ("timeout", timeout), ("input", input)):
            if v is not None:
                params[k] = v
        wait = (timeout or 600) + 15
        return self._call("tools.run", _timeout=wait, **params)

    def tools_resolve(self, tool, environment="host"):
        return self._call("tools.resolve", tool=tool, environment=environment)

    def tools_list(self):
        return self._call("tools.list")

    def close(self):
        try:
            self._s.close()
        except OSError:
            pass
