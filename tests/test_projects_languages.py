import unittest

from tests.helpers import CoreCase
from ndx.core import ValidationError, NDXError
from ndx.language_support import diagnose, complete
from ndx.language_support.completion import current_prefix
from ndx.compiler_manager import expand_command


class TestProjects(CoreCase):
    def test_lifecycle(self):
        pm = self.core.services.get("projects")
        p = pm.create("Demo", "python-hello")
        self.assertTrue((self.core.paths.projects / "Demo" / "main.py").exists())
        self.assertEqual([x.name for x in pm.list()], ["Demo"])
        with self.assertRaises(ValidationError):
            pm.create("Demo", "python-hello")
        with self.assertRaises(ValidationError):
            pm.create("../evil", "empty")
        p = pm.rename(p, "Demo2")
        self.assertEqual(pm.open(p.path).name, "Demo2")
        self.assertEqual(pm.recent()[0].name, "Demo2")
        with self.assertRaises(NDXError):
            pm.delete(p)                       # no confirmation
        self.assertTrue((self.core.paths.projects / "Demo2").exists())
        pm.delete(p, confirmed=True)
        self.assertEqual(pm.list(), [])

    def test_delete_outside_root_refused(self):
        pm = self.core.services.get("projects")
        p = pm.create("A", "empty")
        p.path = "/tmp"
        with self.assertRaises(ValidationError):
            pm.delete(p, confirmed=True)

    def test_all_templates_create(self):
        from ndx.project_manager import TEMPLATES
        pm = self.core.services.get("projects")
        for tid in TEMPLATES:
            pm.create("t-" + tid, tid)


class TestLanguages(CoreCase):
    def test_detect_and_toolchain(self):
        langs = self.core.services.get("languages")
        self.assertEqual(langs.detect("x/main.rs").id, "rust")
        self.assertEqual(langs.detect("a.py").id, "python")
        self.assertIsNone(langs.detect("a.unknownext"))
        self.assertGreaterEqual(len(langs.all()), 12)
        st = langs.toolchain_status(langs.get("rust"))
        self.assertIn("rustc", st)

    def test_diagnostics(self):
        d = diagnose("def f(:\n  pass", "python")
        self.assertEqual(d[0].severity, "error")
        self.assertEqual(diagnose("x = 1\n", "python"), [])
        self.assertTrue(diagnose('{"a": }', "json"))
        self.assertTrue(diagnose("int main() { if (x) { }", "c"))
        self.assertEqual(diagnose('int main() { puts("}"); }', "c"), [])

    def test_completion(self):
        langs = self.core.services.get("languages")
        text = "my_variable = 1\nmy_va"
        pre = current_prefix(text, len(text))
        self.assertEqual(pre, "my_va")
        self.assertIn("my_variable", complete(pre, text, langs.get("python")))
        self.assertIn("import", complete("imp", "", langs.get("python")))

    def test_command_expansion(self):
        self.assertEqual(expand_command("gcc {file} -o {out}", "/p", "/p/main.c"),
                         ["gcc", "/p/main.c", "-o", "/p/main"])


class TestRunner(CoreCase):
    def test_run_streams_output(self):
        import threading
        out, done = [], threading.Event()
        self.core.bus.subscribe("run.output", lambda e: out.append(e.payload["text"]))
        self.core.bus.subscribe("run.finished", lambda e: done.set())
        self.core.services.get("runner").run(["sh", "-c", "echo hi; echo err 1>&2"], ".", label="t")
        self.assertTrue(done.wait(10))
        self.assertIn("hi\n", out)

    def test_run_denied_without_consent(self):
        import threading
        CoreCase.consent_answer = False
        try:
            self.core.bus.subscribe("ui.consent.request", lambda e: None)
        finally:
            CoreCase.consent_answer = True
        # denial path: a consent gateway with no subscribers must deny
        from ndx.core.consent import ConsentGateway
        from ndx.core import EventBus
        self.assertFalse(ConsentGateway(EventBus()).request("t", "m"))


if __name__ == "__main__":
    unittest.main()


class TestRunConsentDoesNotBlockCaller(CoreCase):
    def test_run_returns_immediately_while_consent_pending(self):
        import time
        pending = []
        self.core.bus._subs.clear()
        self.core.bus.subscribe("ui.consent.request", lambda e: pending.append(e.payload["respond"]))
        t0 = time.time()
        self.core.services.get("runner").run(["true"], ".", label="x")
        self.assertLess(time.time() - t0, 1.0)         # caller (UI thread) not blocked
        for _ in range(100):
            if pending:
                break
            time.sleep(0.02)
        self.assertTrue(pending)
        pending[0](False)                              # user denies -> nothing runs

    def test_shutdown_releases_pending_consent(self):
        import threading
        self.core.bus._subs.clear()
        self.core.bus.subscribe("ui.consent.request", lambda e: None)   # UI never answers
        res = {}
        th = threading.Thread(target=lambda: res.setdefault("a", self.core.consent.request("t", "m")))
        th.start()
        time.sleep(0.2) if (time := __import__("time")) else None
        self.core.consent.cancel_all()
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertFalse(res["a"])


class TestEditorSession(CoreCase):
    def test_open_save_restore(self):
        from ndx.interface.editor_session import EditorSession
        pm = self.core.services.get("projects")
        p = pm.create("EdProj", "python-hello")
        langs = self.core.services.get("languages")
        s = EditorSession(self.core.config, langs)
        d = s.open(p.path + "/main.py")
        self.assertEqual(d.language, "python")
        d.text += "# x\n"
        self.assertTrue(d.modified)
        s.save()
        self.assertFalse(d.modified)
        self.assertIn("# x", open(p.path + "/main.py").read())
        s.persist()
        s2 = EditorSession(self.core.config, langs)
        s2.restore()
        self.assertEqual([x.name for x in s2.docs], ["main.py"])
        text, n = EditorSession.replace_all("aXbX", "X", "-")
        self.assertEqual((text, n), ("a-b-", 2))
        with self.assertRaises(ValueError):
            open(p.path + "/bin.dat", "wb").write(b"\x00\x01\x02")
            s2.open(p.path + "/bin.dat")
