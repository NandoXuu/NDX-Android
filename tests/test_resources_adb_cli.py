import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toolchain_helpers import Env      # noqa: E402

from ndx.android_toolkit.adb import AdbClient      # noqa: E402
from ndx.core.events import EventBus      # noqa: E402
from ndx.resource_manager import ResourceManager      # noqa: E402


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.rm = ResourceManager(512, self.bus, None, tempfile.gettempdir())

    def test_track_registers_and_removes(self):
        events = []
        self.bus.subscribe("resources.op_started", lambda e: events.append("start"))
        self.bus.subscribe("resources.op_finished", lambda e: events.append("end"))
        with self.rm.track("download", "x"):
            self.assertEqual([o["name"] for o in self.rm.operations("download")], ["x"])
        self.assertEqual(self.rm.operations(), [])
        self.assertEqual(events, ["start", "end"])

    def test_track_cleans_up_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.rm.track("extract", "y"):
                raise RuntimeError
        self.assertEqual(self.rm.operations(), [])

    def test_check_storage_ok_and_rejected(self):
        ok, info = self.rm.check_storage(tempfile.gettempdir(), 1)
        self.assertTrue(ok)
        ok, info = self.rm.check_storage(tempfile.gettempdir(), 10 ** 18)
        self.assertFalse(ok)
        self.assertEqual(info["quality"], "exact")

    def test_unknown_storage_is_not_a_number(self):
        self.rm.free_storage = lambda p=None: None
        ok, info = self.rm.check_storage("/x", 5)
        self.assertTrue(ok)
        self.assertEqual(info["quality"], "unavailable")
        self.assertIsNone(info["free"])

    def test_admit_ram_budget_and_concurrency(self):
        self.assertTrue(self.rm.admit("server", 100)["ok"])
        r = self.rm.admit("server", 10000)
        self.assertFalse(r["ok"])
        self.assertIn("RAM budget", r["blocked"][0])
        with self.rm.track("compiler", "a"), self.rm.track("process", "b"):
            w = self.rm.admit("process")
            self.assertTrue(w["ok"])
            self.assertTrue(any("resource-heavy" in x for x in w["warnings"]))


FAKE_ADB = """#!/bin/sh
case "$1" in
  version) echo "Android Debug Bridge version 1.0.41"; echo "Version 34.0.0-1";;
  devices) cat "$ADB_FIXTURE_DIR/devices" ;;
  connect) cat "$ADB_FIXTURE_DIR/connect" ;;
  pair) cat "$ADB_FIXTURE_DIR/pair" ;;
  mdns) cat "$ADB_FIXTURE_DIR/mdns" ;;
esac
exit 0
"""


class AdbStateTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.bin = self.d / "bin"
        self.bin.mkdir()
        exe = self.bin / "adb"
        exe.write_text(FAKE_ADB)
        exe.chmod(0o755)
        os.environ["ADB_FIXTURE_DIR"] = str(self.d)
        self.addCleanup(os.environ.pop, "ADB_FIXTURE_DIR", None)
        self.adb = AdbClient(extra_dirs=[str(self.bin)])

    def fx(self, name, text):
        (self.d / name).write_text(text)

    def test_unavailable_when_missing(self):
        a = AdbClient(extra_dirs=[str(self.d / "nowhere")])
        a.path = lambda: None
        st = a.status()
        self.assertEqual(st["state"], "adb_unavailable")
        self.assertIn("ndx tool install adb", st["message"])

    def test_no_device(self):
        self.fx("devices", "List of devices attached\n\n")
        self.assertEqual(self.adb.status()["devices"], [])
        self.assertIn("no device", self.adb.status()["message"])

    def test_authorization_required(self):
        self.fx("devices", "List of devices attached\nABC123\tunauthorized usb:1\n")
        self.assertEqual(self.adb.status()["state"], "authorization_required")

    def test_connected(self):
        self.fx("devices", "List of devices attached\nABC123\tdevice usb:1 model:Pixel\n")
        self.assertEqual(self.adb.status()["state"], "connected")

    def test_offline_is_disconnected(self):
        self.fx("devices", "List of devices attached\nABC123\toffline\n")
        self.assertEqual(self.adb.status()["state"], "device_disconnected")

    def test_connect_pairing_required_and_connected(self):
        self.fx("connect", "failed to authenticate to 192.168.0.5:37000\n")
        self.assertEqual(self.adb.connect_status("192.168.0.5:37000")["state"], "pairing_required")
        self.fx("connect", "connected to 192.168.0.5:37000\n")
        self.assertEqual(self.adb.connect_status("192.168.0.5:37000")["state"], "connected")

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.adb.connect_status("a; rm -rf /")
        with self.assertRaises(ValueError):
            self.adb.pair("192.168.0.5:37001", "abc")

    def test_pair_success_text_required(self):
        self.fx("pair", "Successfully paired to 1.2.3.4:5 [guid=x]\n")
        ok, _ = self.adb.pair("192.168.0.5:37001", "123456")
        self.assertTrue(ok)
        self.fx("pair", "Failed: wrong password\n")
        self.assertFalse(self.adb.pair("192.168.0.5:37001", "123456")[0])

    def test_mdns_discovery(self):
        self.fx("mdns", "List of discovered mdns services\nadb-abc\t_adb-tls-pairing._tcp.\t10.0.0.2:4444\n"
                        "adb-abc\t_adb-tls-connect._tcp.\t10.0.0.2:5555\n")
        kinds = [(f["kind"], f["address"]) for f in self.adb.discover()]
        self.assertEqual(kinds, [("pairing", "10.0.0.2:4444"), ("connect", "10.0.0.2:5555")])

    def test_diagnostics_lists_checks(self):
        self.fx("devices", "List of devices attached\n")
        names = [d["name"] for d in self.adb.diagnostics()]
        self.assertEqual(names, ["adb executable", "adb version", "connection"])


class ToolCliTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.env.publish("mytool", "1.2.3")

    def run_cli(self, *argv):
        from ndx.toolchain_manager.cli import ToolCli
        tc = self.env.tc

        class Svc:
            def require(self, name):
                return tc

        class Core:
            services = Svc()
            consent = self.env.consent
        buf = io.StringIO()
        code = ToolCli(Core()).run(list(argv), buf.write)
        return code, buf.getvalue()

    def test_list_install_remove_cycle(self):
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("mytool", out)
        code, out = self.run_cli("install", "mytool")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.env.paths.bin / "mytool").exists())
        code, out = self.run_cli("remove", "mytool")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.env.paths.bin / "mytool").exists())

    def test_unknown_tool_is_an_error(self):
        code, out = self.run_cli("install", "does-not-exist")
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
