import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toolchain_helpers import Env          # noqa: E402
from test_linux_integration import FAKE_PROOT, rootfs_tar   # noqa: E402

from ndx.core.errors import NDXError, PermissionDenied, ValidationError   # noqa: E402
from ndx.linux_manager import LinuxManager          # noqa: E402
from ndx.termux_integration import TermuxIntegration, TermuxTransport, TERMUX_PREFIX   # noqa: E402
from ndx.terminal import TerminalBridge, BackendUnavailable   # noqa: E402
from ndx.toolchain_manager import ToolRunner         # noqa: E402


class FakeTransport(TermuxTransport):
    def __init__(self, installed=True, permission=True, handler=None):
        self.installed, self.permission, self.calls = installed, permission, []
        self.handler = handler or (lambda path, args: {"stdout": "ndx-ok\n", "stderr": "", "exit_code": 0})

    def package_installed(self, package="com.termux"):
        return self.installed if package == "com.termux" else False

    def permission_granted(self):
        return self.permission

    def execute(self, path, args, cwd=None, timeout=60):
        self.calls.append((path, list(args), cwd))
        return self.handler(path, args)


def answering_probe(fn):
    """Handler that answers the readiness probe like a healthy Termux and delegates everything else to fn."""
    def handler(path, args):
        if "echo ndx-ok" in args:
            return {"stdout": "ndx-ok\n", "stderr": "", "exit_code": 0}
        return fn(path, args)
    return handler


class BridgeCase(unittest.TestCase):
    transport = None

    def setUp(self):
        self.e = Env()
        self.tx = TermuxIntegration(self.e.bus, self.e.config, None, transport=self.transport or FakeTransport())
        self.e.tc.attach_termux(self.tx)
        self.lm = LinuxManager(self.e.paths, self.e.bus, None, self.e.consent, None, toolchain=self.e.tc,
                               resources=self.e.resources, downloader=self.e.tc.downloader)
        self.e.tc.attach_linux(self.lm)
        self.b = TerminalBridge(self.e.paths, self.e.tc, self.lm, self.tx, None, self.e.resources, None, self.e.bus,
                                self.e.config)

    def tearDown(self):
        self.b.terminate_all()
        self.e.close()

    def proot_env(self, eid="a1"):
        p = self.e.paths.bin / "proot"
        p.write_text(FAKE_PROOT)
        os.chmod(p, 0o755)
        self.lm.refresh_status()
        self.lm.install("alpine", env_id=eid, archive=str(rootfs_tar(Path(self.e.tmp.name) / "r.tgz")))


class TestRun(BridgeCase):
    def test_exit_codes_stdout_stderr_separate(self):
        r = self.b.run(["sh", "-c", "echo out; echo err >&2; exit 3"])
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr.strip()), (3, "out", "err"))

    def test_env_cwd_stdin(self):
        d = Path(self.e.tmp.name)
        r = self.b.run(["sh", "-c", "echo $FOO; pwd; cat"], cwd=str(d), env={"FOO": "bar"}, stdin="piped")
        out = r.stdout.split("\n")
        self.assertEqual(out[0], "bar")
        self.assertEqual(os.path.realpath(out[1]), os.path.realpath(d))
        self.assertIn("piped", r.stdout)

    def test_timeout(self):
        t0 = time.time()
        r = self.b.run(["sleep", "10"], timeout=0.4)
        self.assertTrue(r.timed_out)
        self.assertLess(time.time() - t0, 5)

    def test_cancellation_kills_process_group(self):
        ev = threading.Event()
        threading.Timer(0.3, ev.set).start()
        t0 = time.time()
        r = self.b.run(["sh", "-c", "sleep 30 & sleep 30"], cancel=ev)
        self.assertTrue(r.cancelled)
        self.assertLess(time.time() - t0, 5)

    def test_rejects_shell_strings_bad_env_bad_cwd(self):
        for bad in ("ls -la", [], [1], ["a\0b"]):
            with self.assertRaises(ValidationError):
                self.b.run(bad)
        with self.assertRaises(ValidationError):
            self.b.run(["true"], env={"1X": "y"})
        with self.assertRaises(ValidationError):
            self.b.run(["true"], cwd="relative/dir")
        with self.assertRaises(ValidationError):
            self.b.run(["true"], environment="nonexistent")

    def test_missing_tool_suggests_installation_when_known(self):
        self.e.publish("mytool", "1.0.0")
        r = self.b.run(["mytool"])
        self.assertEqual(r.returncode, 127)
        self.assertIn("ndx tool install mytool", r.error)
        r2 = self.b.run(["no-such-binary-xyz"])
        self.assertEqual(r2.returncode, 127)

    def test_managed_tool_runs_and_runtimes_have_separate_path(self):
        self.e.publish("mytool", "1.2.3")
        self.e.tc.install("mytool")
        self.assertIn("mytool 1.2.3", self.b.run(["mytool", "--version"]).stdout)
        host = self.b.run(["sh", "-c", "echo $PATH"]).stdout.strip()
        android = self.b.run(["sh", "-c", "echo $PATH"], environment="android").stdout.strip()
        self.assertIn(str(self.e.paths.bin), host)
        self.assertNotIn(str(self.e.paths.bin), android)
        self.assertFalse(self.b.run(["mytool"], environment="android").ok)      # not visible in the android runtime

    def test_environments_listing_is_honest(self):
        rows = {r["id"]: r for r in self.b.environments()}
        self.assertTrue(rows["host"]["available"])
        self.assertFalse(rows["termux"]["available"] and False)
        self.proot_env()
        self.assertTrue({r["id"]: r for r in self.b.environments()}["a1"]["available"])


class TestSessions(BridgeCase):
    def test_pty_interactive(self):
        s = self.b.open("host", argv=["sh"])
        self.assertTrue(s.pty and s.alive())
        s.write("echo hello-$((1+2))\n")
        self.assertIn("hello-3", s.read_until("hello-3", 5))
        s.write("exit 5\n")
        self.assertEqual(s.wait(5), 5)
        self.assertEqual(s.status()["state"], "exited")
        with self.assertRaises(NDXError):
            s.write("x")

    def test_terminate_and_session_timeout(self):
        s = self.b.open("host", argv=["sleep", "30"])
        self.assertEqual(s.status()["state"], "running")
        s.terminate()
        self.assertIsNotNone(s.wait(5))
        self.assertEqual(s.status()["state"], "cancelled")
        s2 = self.b.open("host", argv=["sleep", "30"], timeout=0.3)
        self.assertIsNotNone(s2.wait(5))
        self.assertEqual(s2.status()["state"], "timed_out")

    def test_pipe_session_has_stdin_stdout_stderr_and_exit_code(self):
        s = self.b.open("host", argv=["sh", "-c", "read x; echo got $x; echo oops >&2; exit 4"], pty=False)
        s.write("a\n")
        self.assertEqual(s.wait(5), 4)
        time.sleep(0.1)
        self.assertIn("got a", s.read(0.5))
        self.assertIn("oops", s.read_stderr())

    def test_sessions_listed_and_closed(self):
        s = self.b.open("host", argv=["cat"])
        self.assertEqual([x["id"] for x in self.b.list_sessions()], [s.id])
        s.close()
        self.assertEqual(self.b.list_sessions(), [])

    def test_linux_environment_session_and_run(self):
        self.proot_env()
        r = self.b.run(["echo", "from-linux"], environment="a1")
        self.assertEqual(r.stdout.strip(), "from-linux")
        self.assertEqual(self.b.run(["echo", "x"], environment="linux:a1").returncode, 0)
        s = self.b.open("a1", argv=["sh"])
        s.write("echo inside-env\n")
        self.assertIn("inside-env", s.read_until("inside-env", 5))
        s.close()

    def test_proot_backend_directly_and_unknown_env(self):
        self.proot_env()
        rootfs = str(self.e.paths.environments / "a1" / "rootfs")
        self.assertEqual(self.b.run(["echo", "bare"], environment="proot:" + rootfs).stdout.strip(), "bare")
        with self.assertRaises(ValidationError):
            self.b.run(["echo"], environment="proot:relative")
        with self.assertRaises(ValidationError):
            self.b.run(["echo"], environment="../evil")

    def test_missing_proot_is_reported_not_crashed(self):
        self.proot_env()
        os.remove(self.e.paths.bin / "proot")
        r = self.b.run(["echo", "x"], environment="a1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("proot", r.error)


class TestTermux(BridgeCase):
    def test_backend_runs_command_by_argument_array(self):
        r = self.b.run(["git", "status", "--short"], environment="termux")
        self.assertEqual(r.stdout, "ndx-ok\n")
        path, args, _ = self.tx.transport.calls[-1]
        self.assertEqual((path, args), (TERMUX_PREFIX + "/bin/git", ["status", "--short"]))

    def test_unsupported_operations_are_refused_honestly(self):
        with self.assertRaises(BackendUnavailable):
            self.b.open("termux")
        with self.assertRaises(ValidationError):
            self.b.run(["ls"], environment="termux", env={"A": "b"})
        with self.assertRaises(ValidationError):
            self.b.run(["ls"], environment="termux", stdin="x")

    def test_command_names_are_validated(self):
        for bad in ("../../bin/sh", "a b", "-x", "/data/data/com.termux/files/usr/bin/sh", ""):
            with self.assertRaises(ValidationError):
                self.tx.run(bad)
        with self.assertRaises(ValidationError):
            self.tx.run("ls", cwd="/etc")                               # a host path is not a Termux path

    def test_exit_code_and_error_are_propagated(self):
        self.tx.transport.handler = answering_probe(lambda p, a: {"stdout": "", "stderr": "nope", "exit_code": 2})
        r = self.b.run(["ls"], environment="termux")
        self.assertEqual((r.returncode, r.stderr), (2, "nope"))
        self.tx.transport.handler = answering_probe(lambda p, a: {"stdout": "", "stderr": "", "exit_code": None, "err": 3, "errmsg": "boom"})
        r = self.b.run(["ls"], environment="termux")
        self.assertEqual(r.returncode, 126)
        self.assertIn("boom", r.error)

    def test_cancel_returns_without_claiming_the_remote_process_died(self):
        gate = threading.Event()
        self.tx.transport.handler = answering_probe(lambda p, a: (gate.wait(3), {"stdout": "", "stderr": "", "exit_code": 0})[1])
        ev = threading.Event()
        threading.Timer(0.2, ev.set).start()
        r = self.b.run(["sleep", "5"], environment="termux", cancel=ev)
        gate.set()
        self.assertTrue(r.cancelled)
        self.assertIn("may still be running", r.error)


class TestTermuxStates(unittest.TestCase):
    def mk(self, **kw):
        return TermuxIntegration(transport=FakeTransport(**kw))

    def test_states_and_guidance(self):
        self.assertEqual(self.mk(installed=False).get_status()["state"], "not_installed")
        st = self.mk(permission=False).get_status()
        self.assertEqual(st["state"], "permission_required")
        self.assertTrue(any("allow-external-apps" in g for g in st["guidance"]))
        self.assertEqual(self.mk(installed=None).get_status()["state"], "unknown_visibility")

    def test_probe_ready_vs_no_response(self):
        t = self.mk()
        self.assertEqual(t.get_status(probe=True)["state"], "ready")
        t2 = self.mk(handler=lambda p, a: {"stdout": "", "stderr": "", "exit_code": None, "err": 4, "errmsg": "no result", "timed_out": True})
        st = t2.get_status(probe=True)
        self.assertEqual(st["state"], "no_response")
        self.assertTrue(any("termux.properties" in g for g in st["guidance"]))

    def test_has_command_is_cached_and_never_raises_when_not_ready(self):
        t = self.mk(handler=lambda p, a: {"stdout": "/data/data/com.termux/files/usr/bin/git\n", "stderr": "", "exit_code": 0}
                    if "command -v" in " ".join(a) else {"stdout": "ndx-ok\n", "stderr": "", "exit_code": 0})
        self.assertFalse(t.has_command("git"))                       # not probed yet -> False, no Android call
        t.get_status(probe=True)
        self.assertTrue(t.has_command("git"))
        n = len(t.transport.calls)
        t.has_command("git")
        self.assertEqual(len(t.transport.calls), n)
        self.assertFalse(t.has_command("bad name"))

    def test_disabled_by_setting(self):
        from ndx.core.config import ConfigManager
        e = Env()
        try:
            e.config.set("termux.enabled", False)
            t = TermuxIntegration(config=e.config, transport=FakeTransport())
            with self.assertRaises(NDXError):
                t.run("ls")
        finally:
            e.close()


class TestToolRunner(BridgeCase):
    def setUp(self):
        super().setUp()
        self.runner = ToolRunner(self.b, self.e.tc, None, self.e.consent, None, self.e.config, self.e.paths)

    def test_script_needs_approval_once_per_tool_and_environment(self):
        for _ in range(2):
            self.assertTrue(self.runner.run("echo", ["hi"], caller="script:x").ok)
        self.assertEqual(len([t for t in self.e.asked if "echo" in t]), 1)
        self.runner.run("echo", ["hi"], environment="android", caller="script:x")
        self.assertEqual(len([t for t in self.e.asked if "echo" in t]), 2)

    def test_denied_and_no_consent_channel(self):
        self.e.answer = False
        with self.assertRaises(PermissionDenied):
            self.runner.run("echo", ["x"], caller="script:x")
        r2 = ToolRunner(self.b, self.e.tc, None, None, None, self.e.config, self.e.paths)
        with self.assertRaises(PermissionDenied):
            r2.run("echo", ["x"], caller="script:x")

    def test_user_actions_are_trusted_and_scripts_are_confined(self):
        self.assertTrue(self.runner.run("echo", ["x"], caller="user").ok)
        with self.assertRaises(PermissionDenied):
            self.runner.run("echo", ["x"], cwd="/", caller="script:x")
        ok_cwd = self.e.paths.projects
        self.assertTrue(self.runner.run("pwd", cwd=str(ok_cwd), caller="script:x").ok)

    def test_validation(self):
        for tool in ("../bin/sh", "/bin/sh", "a b", "", None):
            with self.assertRaises(ValidationError):
                self.runner.run(tool, [], caller="user")
        with self.assertRaises(ValidationError):
            self.runner.run("echo", "hello world", caller="user")           # shell string
        for k in ("PATH", "LD_PRELOAD", "PROOT_LOADER", "NDX_IPC_TOKEN"):
            with self.assertRaises(PermissionDenied):
                self.runner.run("echo", [], env={k: "x"}, caller="script:x")
        self.assertTrue(self.runner.run("echo", [], env={"PATH": "/usr/bin:/bin"}, caller="user").ok)

    def test_plugin_capability(self):
        class Perm:
            def require(self, pid, cap):
                if cap != "process.spawn":
                    raise PermissionDenied(cap)
        r = ToolRunner(self.b, self.e.tc, Perm(), self.e.consent, None, self.e.config, self.e.paths)
        self.assertTrue(r.run("echo", ["p"], caller="plugin:demo").ok)
        class Deny:
            def require(self, pid, cap):
                raise PermissionDenied("process.spawn not granted")
        with self.assertRaises(PermissionDenied):
            ToolRunner(self.b, self.e.tc, Deny(), self.e.consent, None, self.e.config, self.e.paths).run(
                "echo", [], caller="plugin:demo")

    def test_result_helpers_and_info(self):
        r = self.runner.run("sh", ["-c", "exit 2"], caller="user")
        self.assertFalse(r.ok)
        with self.assertRaises(NDXError):
            r.check()
        d = r.to_dict()
        self.assertEqual((d["returncode"], d["ok"]), (2, False))
        self.assertTrue(self.runner.resolve_info("sh")["found"])
        self.assertFalse(self.runner.resolve_info("zz-none")["found"])

    def test_cancel_all(self):
        res = {}
        t = threading.Thread(target=lambda: res.update(r=self.runner.run("sleep", ["20"], caller="user")))
        t.start()
        time.sleep(0.4)
        self.runner.cancel_all()
        t.join(6)
        self.assertTrue(res["r"].cancelled)


class TestScriptToIpc(unittest.TestCase):
    """End to end: an EXTERNAL process (a script) calls tools through the authenticated loopback IPC."""

    def test_external_python_script_calls_a_tool_with_a_scoped_token(self):
        import tempfile
        from ndx.core import NDXCore
        tmp = tempfile.mkdtemp()
        core = NDXCore(tmp)
        core.bus.subscribe("ui.consent.request", lambda ev: ev.payload["respond"](True))
        core.start()
        try:
            bindings = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bindings")
            code = ("import sys,os; sys.path.insert(0, %r)\nfrom ndxapp import NdxApp\n"
                    "app = NdxApp()\nr = app.tools_run('echo', ['via-ipc'])\nprint(r['stdout'].strip(), r['ok'])\n"
                    "try:\n    app.kv_set('k','v')\nexcept Exception as e:\n    print('kv-denied')\n") % bindings
            res = core.services.require("bridge").run([sys.executable, "-I", "-c", code], script=True, timeout=30,
                                                      environment="host")
            self.assertIn("via-ipc True", res.stdout, res.stderr)
            self.assertIn("kv-denied", res.stdout)                     # token only has the 'tools' scope
            ipc = core.services.get("ipc")
            self.assertEqual(ipc.sessions(), [])                       # per-launch token was revoked afterwards
        finally:
            core.stop()


if __name__ == "__main__":
    unittest.main()
