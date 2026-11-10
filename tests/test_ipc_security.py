import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bindings"))

from ndx.application_framework import HeadlessBackend, IpcServer  # noqa: E402
from ndx.application_framework.ipc import is_loopback_addr  # noqa: E402
from ndx.core.errors import PermissionDenied  # noqa: E402
from ndxapp import NdxApp, NdxError  # noqa: E402


class Conn:
    """Raw JSON-lines client for protocol-level tests."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.f = self.s.makefile("rwb")
        self.n = 0

    def raw(self, data):
        self.f.write(data)
        self.f.flush()

    def call(self, method, **params):
        self.n += 1
        self.raw((json.dumps({"id": self.n, "method": method, "params": params}) + "\n").encode())
        return self.read()

    def read(self):
        line = self.f.readline()
        return json.loads(line) if line else None

    def closed(self):
        try:
            return self.f.readline() == b""
        except (OSError, socket.timeout):
            return False


class FakeResult:
    def __init__(self, **kw):
        self.d = dict(argv=[], returncode=0, stdout="", stderr="", ok=True, timed_out=False, cancelled=False,
                      duration=0.0, error="")
        self.d.update(kw)

    def to_dict(self):
        return dict(self.d)


class FakeTools:
    def __init__(self):
        self.calls = []

    def run(self, tool, args=(), environment="host", cwd=None, env=None, timeout=None, input=None, caller="script",
            cancel=None, on_output=None):
        self.calls.append(dict(tool=tool, args=args, environment=environment, timeout=timeout, caller=caller))
        if tool == "denied":
            raise PermissionDenied("nope")
        return FakeResult(argv=[tool, *args], stdout="x" * (2 * 1024 * 1024) if tool == "big" else "out")

    def resolve_info(self, tool, environment="host"):
        return {"tool": tool, "environment": environment, "found": True}

    def list_tools(self):
        return [{"id": "git"}]


class FakeServices:
    def __init__(self, tools):
        self.t = tools

    def get(self, name, default=None):
        return self.t if name == "tools" else default


class FakeCore:
    def __init__(self):
        self.tools = FakeTools()
        self.services = FakeServices(self.tools)


class IpcCase(unittest.TestCase):
    kw = {}

    def setUp(self):
        self.core = FakeCore()
        self.kv = {}
        svc = type("S", (), {"kv_get": lambda s, k: self.kv.get(k), "kv_set": lambda s, k, v: self.kv.__setitem__(k, v)})()
        self.srv = IpcServer(HeadlessBackend(), services=svc, core=self.core, **self.kw).start()
        self.addCleanup(self.srv.stop)

    def authed(self, scopes=("ui", "kv"), **kw):
        c = Conn(self.srv.port)
        r = c.call("auth", token=self.srv.issue_token(scopes, **kw))
        self.assertTrue(r["result"]["ok"], r)
        return c


class TestAuth(IpcCase):
    def test_missing_auth_rejected_and_closed(self):
        c = Conn(self.srv.port)
        for _ in range(3):
            r = c.call("window.create", title="x")
            self.assertEqual(r["code"], "auth_required")
        self.assertTrue(c.closed())

    def test_bad_token_and_throttle(self):
        c = Conn(self.srv.port)
        r = c.call("auth", token="wrong")
        self.assertEqual(r["code"], "auth_failed")
        self.assertIn("error", r)
        c.call("auth", token="wrong2")
        c.call("auth", token="wrong3")
        self.assertTrue(c.closed())

    def test_non_string_token(self):
        c = Conn(self.srv.port)
        self.assertEqual(c.call("auth", token=123)["code"], "auth_failed")

    def test_tokens_random_and_sessions_hide_values(self):
        a, b = self.srv.issue_token(label="a"), self.srv.issue_token(label="b")
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 40)
        dump = json.dumps(self.srv.sessions())
        self.assertNotIn(a, dump)
        self.assertIn('"a"', dump)

    def test_unknown_scope_refused(self):
        with self.assertRaises(ValueError):
            self.srv.issue_token(("root",))

    def test_revoked_token(self):
        tok = self.srv.issue_token(("ui",), label="gone")
        c = Conn(self.srv.port)
        self.assertTrue(c.call("auth", token=tok)["result"]["ok"])
        self.assertEqual(self.srv.revoke_token("gone"), 1)
        self.assertEqual(c.call("ui.tree")["code"], "revoked")
        c2 = Conn(self.srv.port)
        self.assertEqual(c2.call("auth", token=tok)["code"], "auth_failed")

    def test_expired_token(self):
        tok = self.srv.issue_token(("ui",), ttl=0.2)
        c = Conn(self.srv.port)
        self.assertTrue(c.call("auth", token=tok)["result"]["ok"])
        time.sleep(0.3)
        self.assertEqual(c.call("ui.tree")["code"], "expired")
        self.assertEqual(Conn(self.srv.port).call("auth", token=tok)["code"], "auth_failed")


class TestIdle(IpcCase):
    kw = {"idle_timeout": 0.4}

    def test_idle_connection_dropped(self):
        c = self.authed()
        time.sleep(0.8)
        self.assertTrue(c.closed())


class TestScopes(IpcCase):
    def test_wrong_scope_forbidden(self):
        c = self.authed(("ui",))
        self.assertEqual(c.call("services.kv_get", key="a")["code"], "forbidden")
        self.assertEqual(c.call("tools.list")["code"], "forbidden")
        self.assertEqual(c.call("server.shutdown")["code"], "forbidden")
        self.assertEqual(self.core.tools.calls, [])

    def test_kv_scope_and_validation(self):
        c = self.authed(("kv",))
        self.assertTrue(c.call("services.kv_set", key="k1", value={"a": 1})["result"])
        self.assertEqual(c.call("services.kv_get", key="k1")["result"], {"a": 1})
        self.assertEqual(c.call("services.kv_get", key="../../etc")["code"], "bad_request")
        self.assertEqual(c.call("ui.tree")["code"], "forbidden")
        self.assertEqual(c.call("services.kv_set", key="k", value="x" * 70000)["code"], "too_large")   # over the line cap
        self.assertTrue(c.closed())

    def test_admin_status(self):
        c = self.authed(("admin",))
        st = c.call("server.status")["result"]
        self.assertTrue(st["require_auth"])
        self.assertEqual(st["clients"], 1)


class TestValidation(IpcCase):
    def test_unknown_method_and_bad_params(self):
        c = self.authed(("ui", "tools"))
        self.assertEqual(c.call("os.system", cmd="id")["code"], "unknown_method")
        self.assertEqual(c.call("node.add", parent="../x", kind="button")["code"], "bad_request")
        self.assertEqual(c.call("node.add", parent="n1", kind="Bad Kind!")["code"], "bad_request")
        self.assertEqual(c.call("node.add")["code"], "bad_request")
        self.assertEqual(c.call("node.set", node="n1", props=[1])["code"], "bad_request")
        self.assertEqual(c.call("tools.run", tool="git", args="status")["code"], "bad_request")
        self.assertEqual(c.call("tools.run", tool="../bin/sh", args=[])["code"], "bad_request")
        self.assertEqual(c.call("tools.run", tool="git", args=[1])["code"], "bad_request")
        self.assertEqual(c.call("tools.run", tool="git", env={"A B": "x"})["code"], "bad_request")
        self.assertEqual(c.call("tools.run", tool="git", timeout=-1)["code"], "bad_request")

    def test_malformed_input_survives(self):
        c = self.authed(("ui",))
        c.raw(b"not json\n")
        self.assertEqual(c.read()["code"], "bad_request")
        c.raw(b"[1,2]\n")
        self.assertEqual(c.read()["code"], "bad_request")
        self.assertIn("result", c.call("ui.tree"))

    def test_no_traceback_leak(self):
        c = self.authed(("ui",))
        r = c.call("node.set", node="n999999", props={"a": 1})
        self.assertNotIn("Traceback", json.dumps(r))
        self.assertIn(r["code"], ("invalid",))


class TestLimits(IpcCase):
    kw = {"max_message_bytes": 2048, "rate_limit": (5, 0.5)}

    def test_oversize_message_drops_connection(self):
        c = self.authed(("ui",))
        c.raw(b'{"id":1,"method":"window.create","params":{"title":"' + b"a" * 5000 + b'"}}\n')
        self.assertEqual(c.read()["code"], "too_large")
        self.assertTrue(c.closed())

    def test_rate_limited(self):
        c = self.authed(("ui",))
        codes = [c.call("ui.tree").get("code") for _ in range(12)]
        self.assertIn("rate_limited", codes)


class TestClients(IpcCase):
    kw = {"max_clients": 2}

    def test_client_limit(self):
        a, b = self.authed(), self.authed()
        c = Conn(self.srv.port)
        r = c.read()
        self.assertEqual(r["code"], "busy")
        a.f.close()
        a.s.close()
        time.sleep(0.3)
        d = Conn(self.srv.port)
        self.assertTrue(d.call("auth", token=self.srv.issue_token())["result"]["ok"])


class TestTools(IpcCase):
    def test_run_forwards_with_caller_and_cap(self):
        c = self.authed(("tools",), label="script-1")
        r = c.call("tools.run", tool="git", args=["status"], timeout=99999)["result"]
        self.assertEqual(r["argv"], ["git", "status"])
        call = self.core.tools.calls[-1]
        self.assertEqual(call["caller"], "ipc:script-1")
        self.assertEqual(call["timeout"], 600.0)
        big = c.call("tools.run", tool="big")["result"]
        self.assertTrue(big["truncated"])
        self.assertEqual(len(big["stdout"]), 1024 * 1024)
        self.assertEqual(c.call("tools.run", tool="denied")["code"], "denied")
        self.assertEqual(c.call("tools.resolve", tool="git")["result"]["found"], True)
        self.assertEqual(c.call("tools.list")["result"], [{"id": "git"}])

    def test_python_client(self):
        app = NdxApp(self.srv.port, token=self.srv.issue_token(("tools", "kv")))
        self.assertEqual(app.tools_run("git", ["log"])["argv"], ["git", "log"])
        app.kv_set("a", 5)
        self.assertEqual(app.kv_get("a"), 5)
        with self.assertRaises(NdxError) as cm:
            app.window("x")
        self.assertEqual(cm.exception.code, "forbidden")
        app.close()

    def test_client_env_defaults(self):
        os.environ["NDX_IPC_PORT"], os.environ["NDX_IPC_TOKEN"] = str(self.srv.port), self.srv.issue_token(("tools",))
        try:
            app = NdxApp()
            self.assertEqual(app.tools_list(), [{"id": "git"}])
            app.close()
        finally:
            del os.environ["NDX_IPC_PORT"], os.environ["NDX_IPC_TOKEN"]
        with self.assertRaises(NdxError):
            NdxApp(self.srv.port)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_client(self):
        script = ("const {NdxApp}=require(%r);(async()=>{const a=await NdxApp.connect();"
                  "const r=await a.toolsRun('git',['status']);console.log(JSON.stringify(r.argv));a.close();})()"
                  ".catch(e=>{console.error(e.message);process.exit(1)})" % str(ROOT / "bindings" / "ndxapp.js"))
        env = dict(os.environ, NDX_IPC_PORT=str(self.srv.port), NDX_IPC_TOKEN=self.srv.issue_token(("tools",)))
        p = subprocess.run(["node", "-e", script], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), ["git", "status"])

    def test_tools_unavailable_without_core_service(self):
        srv = IpcServer(HeadlessBackend(), core=type("C", (), {"services": FakeServices(None)})()).start()
        self.addCleanup(srv.stop)
        c = Conn(srv.port)
        c.call("auth", token=srv.issue_token(("tools",)))
        self.assertEqual(c.call("tools.list")["code"], "unavailable")


class TestNetwork(unittest.TestCase):
    def test_non_loopback_bind_refused(self):
        for host in ("0.0.0.0", "192.168.1.5", "::", "example.com"):
            with self.assertRaises(ValueError):
                IpcServer(HeadlessBackend(), host=host)

    def test_allow_remote_is_explicit(self):
        srv = IpcServer(HeadlessBackend(), host="0.0.0.0", allow_remote=True)
        self.assertTrue(srv.allow_remote)
        srv._srv.server_close()

    def test_peer_check(self):
        for ok in ("127.0.0.1", "127.5.5.5", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(is_loopback_addr(ok), ok)
        for bad in ("10.0.0.2", "192.168.0.1", "8.8.8.8", "::ffff:10.0.0.1", "garbage", ""):
            self.assertFalse(is_loopback_addr(bad), bad)
        srv = IpcServer(HeadlessBackend())
        self.addCleanup(srv._srv.server_close)
        self.assertFalse(srv._peer_ok("10.1.1.1"))
        self.assertTrue(srv._peer_ok("127.0.0.1"))

    def test_require_auth_false_is_explicit(self):
        srv = IpcServer(HeadlessBackend(), require_auth=False).start()
        self.addCleanup(srv.stop)
        c = Conn(srv.port)
        self.assertIn("result", c.call("window.create", title="t"))
        self.assertEqual(c.call("server.status")["code"], "forbidden")      # never grants admin


class TestShutdown(unittest.TestCase):
    def test_secure_shutdown(self):
        srv = IpcServer(HeadlessBackend()).start()
        tok = srv.issue_token(("ui",))
        c = Conn(srv.port)
        c.call("auth", token=tok)
        port = srv.port
        srv.stop()
        self.assertTrue(c.closed())
        self.assertEqual(srv.sessions(), [])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        srv.stop()          # idempotent

    def test_admin_shutdown(self):
        srv = IpcServer(HeadlessBackend()).start()
        c = Conn(srv.port)
        c.call("auth", token=srv.issue_token(("admin",)))
        self.assertTrue(c.call("server.shutdown")["result"])
        self.assertTrue(c.closed())
        time.sleep(0.3)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", srv.port), timeout=1).close()


class TestSubscriptions(IpcCase):
    def test_event_push_and_concurrent_writes(self):
        c = self.authed(("ui",))
        w = c.call("window.create", title="w")["result"]
        b = c.call("node.add", parent=w, kind="button", props={"text": "x"})["result"]
        self.assertTrue(c.call("node.subscribe", node=b, event="click")["result"])
        import threading
        be = self.srv.backend
        ts = [threading.Thread(target=lambda i=i: be.dispatch(b, "click", {"i": i})) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        got = [c.read() for _ in range(20)]
        self.assertTrue(all(m["event"] == "click" and m["node"] == b for m in got))


class TestRealCore(unittest.TestCase):
    def test_with_real_core_if_tools_service_exists(self):
        from tests.helpers import CoreCase

        class T(CoreCase):
            def runTest(self):
                pass
        t = T()
        t.setUp()
        try:
            if t.core.services.get("tools") is None:
                self.skipTest("core has no 'tools' service yet")
            srv = IpcServer(HeadlessBackend(), core=t.core).start()
            try:
                app = NdxApp(srv.port, token=srv.issue_token(("tools",), ttl=60, label="t"))
                self.assertIsInstance(app.tools_list(), list)
                app.close()
            finally:
                srv.stop()
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
