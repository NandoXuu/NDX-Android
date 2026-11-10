import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ndx.server_manager.supervisor import Supervisor, BackgroundLink, HEARTBEAT_STALE_S


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.clock = Clock()
        specs = [{"id": "sleeper", "runtime": "custom", "command": [sys.executable, "-c", "import time; time.sleep(60)"], "port": 1},
                 {"id": "quick", "runtime": "custom", "command": [sys.executable, "-c", "raise SystemExit(3)"], "port": 2},
                 {"id": "static", "runtime": "builtin-static", "command": [], "port": 3}]
        (self.d / "servers.json").write_text(json.dumps(specs))
        self.sup = Supervisor(self.d, self.clock)
        self.link = BackgroundLink(self.d, self.clock)
        self.addCleanup(lambda: self.sup.inbox and [self.sup.stop_server(s, 1) for s in list(self.sup.procs)])

    def test_no_heartbeat_is_not_running_and_delegation_refused(self):
        st = self.link.status()
        self.assertEqual(st["summary"], "not running")
        self.assertFalse(st["alive"])
        with self.assertRaises(RuntimeError):
            self.link.delegate("sleeper")
        self.assertEqual(list((self.d / "service" / "inbox").glob("*.json")), [])

    def test_heartbeat_makes_it_alive_then_stale(self):
        self.sup.tick()
        self.assertEqual(self.link.status()["summary"], "running")
        self.clock.t += HEARTBEAT_STALE_S + 5
        self.assertTrue(self.link.status()["summary"].startswith("stale"))
        self.assertFalse(self.link.alive())

    def test_delegate_start_stop(self):
        self.sup.tick()
        self.link.delegate("sleeper")
        state = self.sup.tick()
        self.assertEqual(state["servers"]["sleeper"]["state"], "running")
        self.assertIn("1 background server", state["notification"])
        self.assertIn("sleeper: running", "\n".join(self.link.status()["details"]))
        self.link.release("sleeper")
        state = self.sup.tick()
        self.assertEqual(state["servers"]["sleeper"]["state"], "stopped")
        self.assertIn("idle", state["notification"])

    def test_exit_is_reported(self):
        self.sup.tick()
        self.link.delegate("quick")
        self.sup.tick()
        for _ in range(50):
            st = self.sup.tick()
            if st["servers"]["quick"]["state"] == "exited":
                break
            time.sleep(0.05)
        self.assertEqual(st["servers"]["quick"]["exit"], 3)
        self.assertIn("exited with code 3", st["servers"]["quick"]["error"])

    def test_inbox_cannot_run_arbitrary_commands(self):
        self.sup.tick()
        marker = self.d / "pwned"
        (self.sup.inbox / "x.json").write_text(json.dumps(
            {"op": "start", "id": "evil", "command": ["touch", str(marker)]}))
        state = self.sup.tick()
        self.assertEqual(state["servers"]["evil"]["state"], "error")
        self.assertFalse(marker.exists())

    def test_static_servers_not_delegable(self):
        self.sup.tick()
        self.link.delegate("static")
        st = self.sup.tick()
        self.assertEqual(st["servers"]["static"]["state"], "error")
        self.assertIn("external-process", st["servers"]["static"]["error"])

    def test_status_never_promises_persistence(self):
        self.sup.tick()
        text = json.dumps(self.link.status()).lower()
        self.assertNotIn("guarantee", text.replace("does not remove", ""))
        self.assertIn("does not remove", text)

    def test_corrupt_inbox_file_is_ignored(self):
        (self.sup.inbox / "bad.json").write_text("{not json")
        self.sup.tick()
        self.assertEqual(list(self.sup.inbox.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
