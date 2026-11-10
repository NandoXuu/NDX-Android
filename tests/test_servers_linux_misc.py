import json
import os
import socket
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from tests.helpers import CoreCase
from ndx.core import ValidationError, NDXError
from ndx.server_manager import ServerSpec
from ndx.linux_manager.proot import extract_rootfs, build_login_argv
from ndx.application_framework import IpcServer, HeadlessBackend, PlatformServices, Window, Label, Button, VBox
from ndx.android_toolkit import explorer, device_info, CrashLogStore, LogcatMonitor, AdbClient
from ndx.android_toolkit.capabilities import AVAILABLE, UNSUPPORTED


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestServers(CoreCase):
    def test_static_server_lifecycle_and_limits(self):
        sm = self.core.services.get("servers")
        www = Path(self.tmp.name) / "www"
        www.mkdir()
        (www / "index.html").write_text("<h1>ok</h1>")
        port = free_port()
        sm.add(ServerSpec(id="s1", name="site", port=port, cwd=str(www), ram_mb=64))
        sm.start_server("s1")
        body = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read()
        self.assertIn(b"ok", body)
        for _ in range(50):
            st = sm.stats("s1")
            if st["requests"]:
                break
            time.sleep(0.05)
        self.assertEqual(st["state"], "running")
        self.assertGreaterEqual(st["requests"], 1)
        self.assertEqual(self.core.services.get("resources").allocations(), {"server:s1": 64})
        sm.restart("s1")
        sm.stop_server("s1")
        self.assertEqual(self.core.services.get("resources").allocations(), {})
        with self.assertRaises(Exception):
            urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=1)

    def test_external_bind_requires_opt_in(self):
        sm = self.core.services.get("servers")
        with self.assertRaises(ValidationError):
            sm.add(ServerSpec(id="x", name="x", host="0.0.0.0"))
        sm.add(ServerSpec(id="y", name="y", host="0.0.0.0", allow_external=True))

    def test_ram_budget_rejects_start(self):
        sm = self.core.services.get("servers")
        sm.add(ServerSpec(id="big", name="big", ram_mb=999999, cwd=self.tmp.name, port=free_port()))
        with self.assertRaises(NDXError):
            sm.start_server("big")
        self.assertEqual(sm.runtimes["big"].state, "failed")

    def test_process_server_logs_and_stop(self):
        sm = self.core.services.get("servers")
        sm.add(ServerSpec(id="p", name="proc", runtime="custom", ram_mb=512, port=free_port(),
                          command=["sh", "-c", "echo started on $PORT; sleep 30"]))
        sm.start_server("p")
        time.sleep(0.6)
        self.assertTrue(any("started on" in l for l in sm.runtimes["p"].logs))
        self.assertIsNotNone(sm.stats("p")["rss"])
        sm.stop_server("p")
        self.assertEqual(sm.runtimes["p"].state, "stopped")

    def test_persistence(self):
        sm = self.core.services.get("servers")
        sm.add(ServerSpec(id="k", name="keep", port=free_port(), cwd=self.tmp.name))
        data = json.loads((self.core.paths.data / "servers.json").read_text())
        self.assertEqual(data[0]["id"], "k")


class TestLinux(CoreCase):
    def make_tar(self, entries):
        p = Path(self.tmp.name) / "r.tar"
        with tarfile.open(p, "w") as tf:
            for name, kind, payload in entries:
                ti = tarfile.TarInfo(name)
                if kind == "file":
                    data = payload.encode()
                    ti.size = len(data)
                    import io
                    tf.addfile(ti, io.BytesIO(data))
                elif kind == "dir":
                    ti.type = tarfile.DIRTYPE
                    tf.addfile(ti)
                elif kind == "sym":
                    ti.type = tarfile.SYMTYPE
                    ti.linkname = payload
                    tf.addfile(ti)
        return p

    def test_extract_safe(self):
        t = self.make_tar([("bin", "dir", None), ("bin/sh", "file", "x"), ("sbin", "sym", "bin")])
        n = extract_rootfs(t, Path(self.tmp.name) / "rootfs")
        self.assertEqual(n, 3)
        self.assertTrue((Path(self.tmp.name) / "rootfs" / "sbin" / "sh").exists())

    def test_extract_blocks_escape_through_symlink(self):
        t = self.make_tar([("evil", "sym", "/tmp"), ("evil/pwned.txt", "file", "x")])
        with self.assertRaises(ValidationError):
            extract_rootfs(t, Path(self.tmp.name) / "rootfs2")

    def test_install_local_archive_and_manage(self):
        lm = self.core.services.get("linux")
        t = self.make_tar([("bin", "dir", None), ("bin/sh", "file", "#!")])
        lm.install("alpine", env_id="a1", archive=str(t), require_backend=False)   # offline import: no PRoot yet
        self.assertEqual(lm.list()[0]["id"], "a1")
        self.assertEqual(lm.package_command("a1", "install", "git"), ["apk", "add", "git"])
        lm.set_env_var("a1", "FOO", "bar")
        with self.assertRaises(ValidationError):
            lm.profile("a1")                          # no proot binary: honest error
        with self.assertRaises(ValidationError):
            lm.install("alpine", env_id="a1", archive=str(t), require_backend=False)
        b = lm.backup("a1", str(Path(self.tmp.name) / "b.tgz"))
        with self.assertRaises(NDXError):
            lm.remove("a1")
        lm.remove("a1", confirmed=True)
        self.assertEqual(lm.list(), [])

    def test_login_argv(self):
        argv = build_login_argv("/x/proot", "/rootfs")
        self.assertIn("--link2symlink", argv)
        self.assertEqual(argv[:2], ["/x/proot", "--link2symlink"])


class TestAI(CoreCase):
    def test_register_and_honest_unavailable(self):
        ai = self.core.services.get("ai")
        f = Path(self.tmp.name) / "m.gguf"
        f.write_bytes(b"\0" * 1024 * 1024)
        m = ai.register("tiny", str(f), ctx=1024)
        self.assertEqual(m.format, "gguf")
        self.assertGreater(ai.estimate_ram_mb(m), 1)
        with self.assertRaises(Exception):
            ai.load(m.id)                              # llama-server not installed
        with self.assertRaises(ValidationError):
            ai.register("nofile", "/nonexistent.gguf")


class TestFramework(unittest.TestCase):
    def test_ipc_roundtrip(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bindings"))
        from ndxapp import NdxApp
        srv = IpcServer(HeadlessBackend()).start()
        try:
            app = NdxApp(srv.port, token=srv.issue_token(("ui",)))   # IPC now requires a session token
            w = app.window("Demo")
            b = app.add(w, "button", text="Go")
            app.set(b, text="Go!")
            tree = app.tree()
            self.assertEqual(tree["children"][0]["props"]["text"], "Go!")
            with self.assertRaises(RuntimeError):
                app._call("nope")
            app.close()
        finally:
            srv.stop()

    def test_headless_dispatch(self):
        hits = []
        btn = Button("Hi", on_click=lambda p: hits.append(p))
        be = HeadlessBackend()
        be.show(Window("t", VBox(Label("x"), btn)))
        be.dispatch(btn.id, "click", 5)
        self.assertEqual(hits, [5])
        self.assertIn("button Hi", be.render_text())

    def test_services_sandbox(self):
        d = tempfile.mkdtemp()
        s = PlatformServices(d)
        s.write_file("a/b.txt", "hi")
        self.assertEqual(s.read_file("a/b.txt"), b"hi")
        with self.assertRaises(PermissionError):
            s.read_file("../../etc/passwd")
        s.kv_set("k", {"x": 1})
        self.assertEqual(s.kv_get("k"), {"x": 1})


class TestAndroidToolkit(unittest.TestCase):
    def test_capabilities_cover_groups_and_statuses(self):
        rows, probes = explorer()
        self.assertEqual({r["group"] for r in rows}, {"Read", "Navigate", "Control"})
        self.assertEqual({r["status"] for r in rows} - {"Available", "Restricted", "Unsupported", "Permission Required"}, set())
        priv = [r for r in rows if "Enable Developer options" in r["name"]][0]
        self.assertEqual(priv["status"], UNSUPPORTED)

    def test_adb_missing_is_reported(self):
        a = AdbClient()
        if not a.available():
            with self.assertRaises(Exception):
                a.devices()
        with self.assertRaises(ValueError):
            AdbClient().pair("bad host", "x")

    def test_crash_store_and_filter(self):
        d = Path(tempfile.mkdtemp())
        cs = CrashLogStore(d)
        self.assertIsNotNone(cs.save_if_crash(["E AndroidRuntime: FATAL EXCEPTION: main"]))
        self.assertIsNone(cs.save_if_crash(["all good"]))
        m = LogcatMonitor(AdbClient(), cs)
        m.lines.extend(["01-01 00:00:00.0 1 1 I tag: hello", "01-01 00:00:00.0 1 1 E tag: broken"])
        self.assertEqual(len(m.filter(level="E")), 1)
        self.assertEqual(len(m.filter(text="HELLO")), 1)

    def test_device_info(self):
        self.assertIn("source", device_info())


if __name__ == "__main__":
    unittest.main()
