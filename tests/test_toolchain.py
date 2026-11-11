import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toolchain_helpers import Env, fake_elf, make_targz, script_tool, sha, manifest, HOST   # noqa: E402

from ndx.core.errors import NDXError, ToolUnavailable, ValidationError     # noqa: E402
from ndx.toolchain_manager import ToolValidator, validate_manifest         # noqa: E402


class TestManifest(unittest.TestCase):
    def ok(self, **kw):
        m = manifest(data=b"x")
        m.update(kw)
        return m

    def test_valid(self):
        self.assertEqual(validate_manifest(self.ok()).id, "mytool")

    def test_rejections(self):
        bad = [dict(id="../evil"), dict(id="UPPER"), dict(executable="/bin/sh"), dict(executable="../x"),
               dict(version="not-a-version"), dict(architectures=["mips"]), dict(bogus=1),
               dict(version_regex="("), dict(requires=["mytool"]), dict(env={"1BAD": "x"})]
        for patch in bad:
            with self.assertRaises(ValidationError, msg=str(patch)):
                validate_manifest(self.ok(**patch))

    def test_download_source_rules(self):
        for url in ("http://%s/a.tgz" % HOST, "https://evil.example/a.tgz"):
            m = self.ok()
            for s in m["install"][0]["sources"].values():
                s["url"] = url
            with self.assertRaises(ValidationError):
                validate_manifest(m)
        m = self.ok()
        m["install"][0]["sources"]["x86_64"]["sha256"] = "zz"
        with self.assertRaises(ValidationError):
            validate_manifest(m)


class TestValidator(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = Path(tempfile.mkdtemp())

    def test_arm64_android_interp_ok(self):
        p = fake_elf(self.d / "a", 0xB7, "/system/bin/linker64")
        self.assertTrue(ToolValidator(True, ["arm64-v8a"]).validate_file(str(p), "elf", "arm64-v8a").ok)

    def test_desktop_linux_binary_rejected_on_android(self):
        p = fake_elf(self.d / "b", 0xB7, "/lib/ld-linux-aarch64.so.1")
        r = ToolValidator(True, ["arm64-v8a"]).validate_file(str(p), "elf", "arm64-v8a")
        self.assertFalse(r.ok)
        self.assertIn("desktop Linux", r.summary())

    def test_termux_prefix_binary_rejected(self):
        p = fake_elf(self.d / "c", 0xB7, "/data/data/com.termux/files/usr/lib/ld.so")
        self.assertIn("Termux", ToolValidator(True, ["arm64-v8a"]).validate_file(str(p), "elf", "arm64-v8a").summary())

    def test_wrong_cpu_rejected(self):
        p = fake_elf(self.d / "d", 0x3E, "/system/bin/linker64")
        r = ToolValidator(True, ["arm64-v8a"]).validate_file(str(p), "elf", "arm64-v8a")
        self.assertFalse(r.ok)
        self.assertIn("x86_64", r.summary())

    def test_html_error_page_and_empty_file(self):
        (self.d / "e").write_text("<html>404</html>")
        (self.d / "f").write_bytes(b"")
        v = ToolValidator(False, ["x86_64"])
        self.assertIn("HTML", v.validate_file(str(self.d / "e")).summary())
        self.assertFalse(v.validate_file(str(self.d / "f")).ok)
        self.assertFalse(v.validate_file(str(self.d / "missing")).ok)


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.tc = self.e.tc

    def tearDown(self):
        self.e.close()

    def test_install_resolve_version_doctor(self):
        self.e.publish("mytool", "1.2.3")
        self.assertFalse(self.tc.is_installed("mytool"))
        res = self.tc.install("mytool")
        self.assertEqual(res["version"], "1.2.3")
        r = self.tc.resolve("mytool", runtime="host")
        self.assertEqual(r.kind, "managed")
        self.assertTrue(r.path.startswith(str(self.e.paths.base / "toolchain" / "mytool")))
        self.assertEqual(self.tc.get_version("mytool"), "1.2.3")
        shim = self.e.paths.bin / "mytool"
        self.assertTrue(shim.is_symlink() and os.access(shim, os.X_OK))
        self.assertTrue(self.tc.environment()["PATH"].startswith(str(self.e.paths.bin)))
        self.assertEqual(self.tc.doctor()["status"], "ok")
        self.assertIn("Install Mytool?", self.e.asked)           # consent was requested (never silent)

    def test_consent_denied_installs_nothing(self):
        self.e.answer = False
        self.e.publish()
        with self.assertRaises(NDXError):
            self.tc.install("mytool")
        self.assertFalse(self.tc.is_installed("mytool"))
        self.assertFalse((self.e.paths.base / "toolchain" / "mytool").exists())

    def test_checksum_mismatch_leaves_nothing_behind(self):
        m, data = self.e.publish()
        for s in m["install"][0]["sources"].values():
            s["sha256"] = "0" * 64
        self.tc.registry.add_user_manifest(m)
        with self.assertRaises(ValidationError) as cm:
            self.tc.install("mytool")
        self.assertIn("checksum mismatch", str(cm.exception))
        self.assertEqual(list(self.tc.staging_dir.iterdir()), [])
        self.assertFalse(self.tc.is_installed("mytool"))
        self.assertFalse((self.e.paths.bin / "mytool").exists())

    def test_unverified_download_needs_its_own_approval(self):
        self.e.publish(pin=False)
        self.e.answer = lambda p: not p["title"].startswith("Install Mytool WITHOUT")   # approve install, deny unverified
        with self.assertRaises(ValidationError) as cm:
            self.tc.install("mytool")
        self.assertIn("without your explicit approval", str(cm.exception))
        self.assertFalse(self.tc.is_installed("mytool"))
        # headless (no consent channel): refused
        with self.assertRaises(ValidationError):
            self.tc.install("mytool", confirm=False)
        # explicit approval of the unverified download
        self.e.answer = True
        res = self.tc.install("mytool")
        self.assertEqual(res["integrity"], "unverified-approved")
        self.assertTrue(any("WITHOUT checksum" in t for t in self.e.asked))
        self.assertTrue(self.tc.registry.install_record("mytool")["sha256"])      # received hash is recorded

    def test_failed_update_rolls_back_to_previous_version(self):
        self.e.publish("mytool", "1.0.0")
        self.tc.install("mytool")
        self.e.publish("mytool", "2.0.0", exe=script_tool("mytool", "2.0.0", exit_code=1))   # new build is broken
        with self.assertRaises(ToolUnavailable):
            self.tc.install("mytool")
        self.assertEqual(self.tc.versions.current("mytool"), "1.0.0")
        self.assertEqual(self.tc.get_version("mytool"), "1.0.0")
        self.assertEqual(self.tc.registry.install_record("mytool")["version"], "1.0.0")
        self.assertEqual(list(self.tc.staging_dir.iterdir()), [])
        self.assertEqual(self.tc.doctor("mytool")["status"], "ok")

    def test_update_keeps_versions_and_use_switches(self):
        self.e.publish("mytool", "1.0.0")
        self.tc.install("mytool")
        self.e.publish("mytool", "2.0.0")
        self.assertEqual([t[0] for t in self.tc.update_check()], ["mytool"])
        self.tc.update()
        self.assertEqual(self.tc.get_version("mytool"), "2.0.0")
        self.assertEqual(self.tc.versions.list_versions("mytool"), ["1.0.0", "2.0.0"])
        self.tc.use("mytool", "1.0.0")
        self.assertEqual(self.tc.get_version("mytool"), "1.0.0")

    def test_remove_cleans_everything_and_respects_dependents(self):
        self.e.publish("basetool", "1.0.0")
        self.e.publish("apptool", "1.0.0", deps=["basetool"])
        self.tc.install("apptool")                         # installs the dependency first
        self.assertTrue(self.tc.is_installed("basetool"))
        with self.assertRaises(NDXError) as cm:
            self.tc.remove("basetool", confirmed=True)
        self.assertIn("required by apptool", str(cm.exception))
        with self.assertRaises(NDXError):
            self.tc.remove("apptool")                      # needs confirmation
        self.tc.remove("apptool", confirmed=True)
        self.tc.remove("basetool", confirmed=True)
        for t in ("apptool", "basetool"):
            self.assertFalse(self.tc.is_installed(t))
            self.assertFalse((self.e.paths.base / "toolchain" / t).exists())
            self.assertFalse((self.e.paths.bin / t).exists() or (self.e.paths.bin / t).is_symlink())

    def test_missing_executable_is_detected(self):
        self.e.publish()
        self.tc.install("mytool")
        os.remove(self.tc.resolve("mytool", runtime="host").path)
        self.assertIsNone(self.tc.resolve("mytool", runtime="host"))
        rep = self.tc.doctor("mytool")
        self.assertEqual(rep["status"], "fail")
        self.assertTrue(any(c["name"] == "executable present" and c["status"] == "fail" for c in rep["tools"][0]["checks"]))

    def test_modified_executable_is_detected(self):
        self.e.publish()
        self.tc.install("mytool")
        p = self.tc.resolve("mytool", runtime="host").path
        os.chmod(p, 0o755)
        Path(p).write_text("#!/bin/sh\necho 'mytool 1.0.0'\n# tampered\n")
        rep = self.tc.doctor("mytool")
        self.assertTrue(any(c["name"] == "file unchanged since install" and c["status"] == "fail"
                            for c in rep["tools"][0]["checks"]))

    def test_android_exec_block_is_reported_not_hidden(self):
        e = Env(probe=(False, "executing files from NDX storage is blocked (targetSdk>=29)"))
        try:
            e.publish()
            with self.assertRaises(ToolUnavailable) as cm:
                e.tc.install("mytool")
            self.assertIn("blocked", str(cm.exception))
            self.assertFalse(e.tc.is_installed("mytool"))
            self.assertEqual(e.tc.doctor()["global"][0]["status"], "fail")
        finally:
            e.close()

    def test_local_file_needs_checksum_or_flag(self):
        self.e.publish()
        f = Path(self.e.tmp.name) / "tool.tgz"
        data = make_targz({"bin/mytool": script_tool()})
        f.write_bytes(data)
        with self.assertRaises(ValidationError):
            self.tc.install("mytool", from_file=str(f))
        with self.assertRaises(ValidationError):
            self.tc.install("mytool", from_file=str(f), sha256="1" * 64)
        res = self.tc.install("mytool", from_file=str(f), sha256=sha(data))
        self.assertEqual(res["integrity"], "user-supplied")

    def test_archive_path_traversal_is_rejected(self):
        data = make_targz({"../../escape": script_tool(), "bin/mytool": script_tool()})
        m = manifest(data=data)
        self.e.web.files[m["install"][0]["sources"]["x86_64"]["url"]] = data
        self.tc.registry.add_user_manifest(m)
        with self.assertRaises(ValidationError):
            self.tc.install("mytool")
        self.assertFalse((self.e.paths.base / "escape").exists())

    def test_host_must_be_allowed_and_https(self):
        with self.assertRaises(ValidationError):
            self.tc.downloader.check_url("https://evil.example/x", [HOST])
        with self.assertRaises(ValidationError):
            self.tc.downloader.check_url("http://%s/x" % HOST, [HOST])

    def test_storage_check_blocks_install(self):
        self.e.publish()
        self.e.resources.check_storage = lambda path, n: (False, {"message": "1 MB free, about 99 MB needed"})
        with self.assertRaises(NDXError) as cm:
            self.tc.install("mytool")
        self.assertIn("not enough storage", str(cm.exception))
        self.assertEqual(self.e.web.requests, [])              # nothing downloaded

    def test_profiles_plan_storage_and_unknown(self):
        self.tc.is_installed = lambda tool, runtime=None: False
        plan = self.tc.plan_profile("cpp")
        self.assertIn("clang", [t["id"] for t in plan["tools"]])
        self.assertGreater(plan["estimated_bytes"], 0)
        with self.assertRaises(ValidationError):
            self.tc.plan_profile("nope")
        self.assertEqual(set(self.tc.profiles()), {"minimal", "python", "cpp", "rust", "android", "linux", "ai", "full"})

    def test_second_install_same_version_needs_force(self):
        self.e.publish()
        self.tc.install("mytool")
        with self.assertRaises(NDXError):
            self.tc.install("mytool")
        self.assertEqual(self.tc.install("mytool", force=True)["version"], "1.0.0")


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.tc = self.e.tc

    def tearDown(self):
        self.e.close()

    def _exe(self, d, name):
        Path(d).mkdir(parents=True, exist_ok=True)
        p = Path(d) / name
        p.write_text("#!/bin/sh\necho x\n")
        os.chmod(p, 0o755)
        return str(p)

    def test_priority_is_configurable(self):
        managed = self._exe(self.e.paths.bin, "zzcmd")
        ext = self._exe(Path(self.e.tmp.name) / "ext", "zzcmd")
        self.e.config.set("toolchain.external_dirs", [str(Path(ext).parent)])
        self.assertEqual(self.tc.resolve_path("zzcmd"), managed)
        self.e.config.set("toolchain.priority", ["external", "managed"])
        self.assertEqual(self.tc.resolve_path("zzcmd"), ext)
        self.e.config.set("toolchain.priority", ["bogus"])               # invalid -> default order
        self.assertEqual(self.tc.resolve_path("zzcmd"), managed)

    def test_project_env_has_precedence_over_system(self):
        proj = Path(self.e.tmp.name) / "proj"
        p = self._exe(proj / ".venv" / "bin", "zzproj")
        self.assertEqual(self.tc.resolve_path("zzproj", project=proj), p)
        self.assertIsNone(self.tc.resolve_path("zzproj"))

    def test_runtimes_never_mix(self):
        self._exe(self.e.paths.bin, "zzhost")
        self.tc.resolver.env_probe = lambda c: [("ubuntu", "/usr/bin/" + c)] if c == "zzlinux" else []
        self.assertIsNone(self.tc.resolve("zzlinux", runtime="host"))
        r = self.tc.resolve("zzlinux", runtime="linux:ubuntu")
        self.assertEqual((r.kind, r.env_id, r.runtime), ("proot", "ubuntu", "linux"))
        self.assertIsNone(self.tc.resolve_path("zzlinux"))              # a path INSIDE proot is never a host path
        lin = self.tc.environment("linux")
        self.assertNotIn(str(self.e.paths.bin), lin["PATH"])
        self.assertEqual(self.tc.environment("termux"), {})
        self.assertIn(str(self.e.paths.bin), self.tc.environment("host")["PATH"])

    def test_native_lib_resolution(self):
        d = Path(self.e.tmp.name) / "nativelibs"
        self._exe(d, "libzznative.so")
        self.tc.resolver._native = lambda: str(d)
        r = self.tc.resolve("zznative", runtime="host")
        self.assertEqual((r.kind, r.source), ("managed", "APK native library"))

    def test_resolve_unknown_returns_none(self):
        self.assertIsNone(self.tc.resolve("definitely-not-a-command-xyz"))
        self.assertFalse(self.tc.is_installed("definitely-not-a-command-xyz"))


if __name__ == "__main__":
    unittest.main()
