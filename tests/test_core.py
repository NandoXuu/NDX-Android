import json
import os
import threading
import unittest

from tests.helpers import CoreCase
from ndx.core import EventBus, ConfigManager, TaskManager, ValidationError
from ndx.core.registry import Module, ModuleRegistry
from ndx.resource_manager import ResourceManager, RequestGate, ResourceMonitor
from ndx.security import safe_join, PermissionService
from ndx.utilities import match_bracket, human_size


class TestBus(unittest.TestCase):
    def test_wildcards_and_isolation(self):
        bus, got = EventBus(), []
        bus.subscribe("project.*", lambda e: got.append(e.name))
        bus.subscribe("project.created", lambda e: 1 / 0)      # must not break others
        bus.publish("project.created")
        bus.publish("other.x")
        self.assertEqual(got, ["project.created"])

    def test_main_dispatcher(self):
        queued = []
        bus = EventBus(main_dispatcher=queued.append)
        hit = []
        bus.subscribe("a", lambda e: hit.append(1), on_main=True)
        bus.publish("a")
        self.assertEqual(hit, [])
        queued[0]()
        self.assertEqual(hit, [1])


class TestConfig(CoreCase):
    def test_roundtrip_and_corrupt(self):
        self.core.config.set("editor.font_size", 18)
        c2 = ConfigManager(self.core.paths.data / "config.json")
        self.assertEqual(c2.get("editor.font_size"), 18)
        self.assertEqual(c2.get("nope.x", "d"), "d")
        (self.core.paths.data / "bad.json").write_text("{not json")
        c3 = ConfigManager(self.core.paths.data / "bad.json")
        self.assertEqual(c3.get("ui.theme"), "dark")


class TestModules(unittest.TestCase):
    def test_failing_module_isolated(self):
        class Bad(Module):
            name = "bad"
            def start(self, core): raise RuntimeError("boom")
        class Good(Module):
            name = "good"
        reg = ModuleRegistry()
        reg.register(Bad()); reg.register(Good())
        reg.start_all()
        self.assertEqual(reg.get("bad").status, "failed")
        self.assertEqual(reg.get("good").status, "running")


class TestTasks(unittest.TestCase):
    def test_runs_off_thread_and_callback(self):
        done, main = threading.Event(), threading.get_ident()
        seen = {}
        tm = TaskManager()
        def work():
            seen["t"] = threading.get_ident()
            return 7
        tm.submit(work, on_done=lambda r: (seen.__setitem__("r", r), done.set()))
        self.assertTrue(done.wait(5))
        self.assertEqual(seen["r"], 7)
        self.assertNotEqual(seen["t"], main)
        tm.shutdown()


class TestBoot(CoreCase):
    def test_all_modules_started_or_honestly_unavailable(self):
        for m in self.core.modules.all():
            self.assertIn(m.status, ("running", "unavailable"), (m.name, m.status, m.error))
        self.assertEqual(self.core.services.get("linux").status, "unavailable")   # no proot here


class TestResources(unittest.TestCase):
    def test_budget(self):
        rm = ResourceManager(100)
        self.assertTrue(rm.reserve("a", 60))
        self.assertFalse(rm.reserve("b", 60))
        rm.release("a")
        self.assertTrue(rm.reserve("b", 60))

    def test_gate_rejects(self):
        g = RequestGate(max_concurrency=1, queue_limit=0, timeout=0.1)
        self.assertTrue(g.acquire())
        self.assertFalse(g.acquire())
        self.assertEqual(g.stats()["rejected"], 1)
        g.release()
        self.assertTrue(g.acquire())

    def test_monitor_quality_flags(self):
        snap = ResourceMonitor("/").snapshot()
        self.assertIn(snap["gpu"]["info"]["quality"], ("unavailable",))
        self.assertIn(snap["memory"]["total"]["quality"], ("exact", "unavailable"))


class TestSecurity(unittest.TestCase):
    def test_safe_join(self):
        self.assertTrue(str(safe_join("/tmp/x", "a", "b")).endswith("a/b"))
        with self.assertRaises(ValidationError):
            safe_join("/tmp/x", "..", "etc")

    def test_permissions(self, ):
        import tempfile, pathlib
        d = pathlib.Path(tempfile.mkdtemp())
        ps = PermissionService(d / "p.json")
        self.assertFalse(ps.check("p", "fs.write"))
        ps.grant("p", ["fs.read"])
        self.assertTrue(ps.check("p", "fs.read"))
        with self.assertRaises(Exception):
            ps.require("p", "fs.write")
        with self.assertRaises(ValueError):
            ps.grant("p", ["root.access"])


class TestUtils(unittest.TestCase):
    def test_brackets(self):
        t = "f(a[1], {x})"
        self.assertEqual(match_bracket(t, 1), 11)
        self.assertEqual(match_bracket(t, 11), 1)
        self.assertEqual(match_bracket(t, 0), -1)
        self.assertEqual(human_size(2048), "2.0 KB")


if __name__ == "__main__":
    unittest.main()
