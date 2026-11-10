import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ndx.core.errors import PermissionDenied, ToolUnavailable, ValidationError
from ndx.python_runtime import PythonRuntime, parse_requirements
from ndx.python_runtime.embedded import EmbeddedRunner
from ndx.terminal import TerminalBridge


class NoTc:
    def resolve(self, *a, **k):
        return None

    def resolve_path(self, *a, **k):
        return None

    def get_version(self, *a, **k):
        return None


class FakeBridge:
    def __init__(self):
        self.calls = []

    def run(self, argv, env_name, cwd, env, timeout, stdin=None, cancel=None, on_output=None, **kw):
        self.calls.append((argv, env_name, cwd))
        from ndx.terminal.process import CommandResult
        return CommandResult(argv=argv, returncode=0, stdout="ok")


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.proj = Path(self.d)

    def script(self, body, name="main.py"):
        p = self.proj / name
        p.write_text(body)
        return str(p)

    def rt(self, mode="inprocess", bridge=None):
        return PythonRuntime(NoTc(), bridge or FakeBridge(), embedded_mode=mode)

    def test_requirements_parsing(self):
        r = self.proj / "requirements.txt"
        r.write_text("# c\nrequests>=2\n-r other.txt\ngit+https://x/y\nnumpy # x\n\n")
        self.assertEqual(parse_requirements(r), ["requests", "numpy"])

    def test_embedded_runs_with_args_and_output(self):
        s = self.script("import sys\nprint('hi', sys.argv[1:])\nsys.stderr.write('e\\n')\n")
        cwd = os.getcwd()
        r = self.rt().run_script(s, ["a"], "embedded")
        self.assertEqual(r.returncode, 0)
        self.assertIn("hi ['a']", r.stdout)
        self.assertIn("e", r.stderr)
        self.assertEqual(os.getcwd(), cwd)

    def test_embedded_exit_code_and_traceback(self):
        r = self.rt().run_script(self.script("import sys; sys.exit(3)"), target="embedded")
        self.assertEqual(r.returncode, 3)
        r = self.rt().run_script(self.script("raise ValueError('boom')", "b.py"), target="embedded")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ValueError: boom", r.stderr)

    def test_embedded_restores_environment(self):
        s = self.script("import os; os.environ['NDX_T']='1'; print(os.environ.get('X'))")
        r = self.rt().run_script(s, target="embedded", env={"X": "y"})
        self.assertIn("y", r.stdout)
        self.assertNotIn("NDX_T", os.environ)
        self.assertNotIn("X", os.environ)

    def test_embedded_timeout_and_cancel(self):
        s = self.script("import time\nwhile True:\n    time.sleep(0.01)\n")
        t0 = time.time()
        r = self.rt().run_script(s, target="embedded", timeout=0.3)
        self.assertTrue(r.timed_out)
        self.assertLess(time.time() - t0, 5)
        ev = threading.Event()
        threading.Timer(0.2, ev.set).start()
        r = self.rt().run_script(s, target="embedded", cancel=ev)
        self.assertTrue(r.cancelled)

    def test_embedded_requires_consent_for_scripts(self):
        s = self.script("print(1)")
        with self.assertRaises(PermissionDenied):
            self.rt().run_script(s, target="embedded", caller="script")

    def test_embedded_busy_is_refused_not_deadlocked(self):
        self.assertTrue(EmbeddedRunner._run_lock.acquire(timeout=1))
        try:
            import ndx.python_runtime.embedded as e
            orig = threading.Lock.acquire
            r = EmbeddedRunner()
            # shorten wait: acquire with timeout=5 is inside; just assert flag behaviour via a patched lock
            class L:
                def acquire(self, timeout=None): return False
                def release(self): pass
            EmbeddedRunner._run_lock, saved = L(), EmbeddedRunner._run_lock
            try:
                res = r.run(self.script("print(1)"))
            finally:
                EmbeddedRunner._run_lock = saved
            self.assertEqual(res["returncode"], 1)
            self.assertIn("busy", res["error"])
        finally:
            EmbeddedRunner._run_lock.release()

    def test_subprocess_mode_uses_bridge_with_sys_executable(self):
        b = FakeBridge()
        self.rt("subprocess", b).run_script(self.script("print(1)"), target="embedded")
        self.assertEqual(b.calls[0][0][0], sys.executable)

    def test_targets_and_select(self):
        rt = self.rt()
        ids = {t["id"]: t for t in rt.targets(str(self.proj))}
        self.assertTrue(ids["embedded"]["available"])
        self.assertFalse(ids["host"]["available"])
        self.assertFalse(ids["venv"]["available"])
        self.assertFalse(ids["termux"]["available"])
        self.assertEqual(rt.select(str(self.proj))[0], "embedded")
        with self.assertRaises(ToolUnavailable):
            rt.select(str(self.proj), "host")

    def test_select_warns_on_missing_requirements(self):
        (self.proj / "requirements.txt").write_text("definitely-not-installed-pkg-xyz\n")
        t, why = self.rt().select(str(self.proj))
        self.assertEqual(t, "embedded")
        self.assertTrue(why[0].startswith("WARNING"))

    def test_venv_preferred_when_present(self):
        py = self.proj / ".venv" / "bin" / "python3"
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
        self.assertEqual(self.rt().select(str(self.proj))[0], "venv")
        b = FakeBridge()
        self.rt(bridge=b).run_script(self.script("print(1)"), target="venv")
        self.assertEqual(b.calls[0][0][0], str(py))

    def test_termux_rejects_private_storage_and_linux_rejects_outside_project(self):
        s = self.script("print(1)")
        with self.assertRaises(ValidationError):
            self.rt().run_script(s, target="termux")
        other = tempfile.mkdtemp()
        with self.assertRaises(ValidationError):
            self.rt().run_script(s, target="linux:x", cwd=other)

    def test_linux_maps_project_path(self):
        b = FakeBridge()
        self.script("print(1)", "sub.py")
        self.rt(bridge=b).run_script(str(self.proj / "sub.py"), target="linux:u1", cwd=str(self.proj))
        self.assertEqual(b.calls[0][0][:3], ["python3", "-u", "/project/sub.py"])

    def test_bad_args_and_missing_script(self):
        with self.assertRaises(ValidationError):
            self.rt().run_script(str(self.proj / "nope.py"))
        with self.assertRaises(ValidationError):
            self.rt().run_script(self.script("1"), "a b", "embedded")

    def test_cli(self):
        out = []
        rt = self.rt()
        self.assertEqual(rt.cli(["targets", str(self.proj)], out.append), 0)
        self.assertIn("embedded", "".join(out))
        out.clear()
        self.assertEqual(rt.cli(["run", "--target", "embedded", self.script("print('zz')")], out.append), 0)
        self.assertIn("zz", "".join(out))


if __name__ == "__main__":
    unittest.main()
