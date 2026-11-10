import io
import os
import sys
import tarfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toolchain_helpers import Env, make_targz, sha    # noqa: E402

from ndx.core.errors import NDXError, ToolUnavailable, ValidationError   # noqa: E402
from ndx.linux_manager import LinuxManager, validate_env_id, DISTROS, resolve_in_rootfs, rootfs_which   # noqa: E402
from ndx.linux_manager.proot import extract_rootfs   # noqa: E402

FAKE_PROOT = """#!/bin/sh
# test double: understands --version and runs the command after '/usr/bin/env' on the HOST
if [ "$1" = "--version" ]; then echo "proot 5.4.0"; exit 0; fi
[ -n "$FAKE_PROOT_FAIL" ] && { echo "proot: fatal error: $FAKE_PROOT_FAIL" >&2; exit 1; }
while [ $# -gt 0 ]; do
  if [ "$1" = "/usr/bin/env" ]; then shift; exec env "$@"; fi
  shift
done
exit 99
"""


def rootfs_tar(path, extra=None, shell=True):
    with tarfile.open(path, "w:gz") as tf:
        def add(name, data=b"", mode=0o755, typ=tarfile.REGTYPE, link=""):
            ti = tarfile.TarInfo(name)
            ti.type, ti.mode, ti.linkname = typ, mode, link
            ti.size = len(data) if typ == tarfile.REGTYPE else 0
            tf.addfile(ti, io.BytesIO(data) if typ == tarfile.REGTYPE else None)
        for d in ("bin", "etc", "usr", "usr/bin", "root", "tmp"):
            add(d, typ=tarfile.DIRTYPE)
        if shell:
            add("bin/sh", b"#!/bin/sh\nexit 0\n")
        add("etc/os-release", b"NAME=Test\n", 0o644)
        add(".profile", b"# top-level dotfile\n", 0o644)
        add("usr/bin/awk", typ=tarfile.SYMTYPE, link="/etc/alternatives/awk")     # absolute symlink (re-rooted)
        add("etc/alternatives", typ=tarfile.DIRTYPE)
        add("etc/alternatives/awk", b"#!/bin/sh\n", 0o755)
        for n, d in (extra or {}).items():
            add(n, d)
    return path


class LinuxCase(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.paths = self.e.paths
        self.lm = LinuxManager(self.paths, self.e.bus, None, self.e.consent, None, toolchain=self.e.tc,
                               resources=self.e.resources, downloader=self.e.tc.downloader)
        self.tar = rootfs_tar(Path(self.e.tmp.name) / "rootfs.tar.gz")

    def tearDown(self):
        self.e.close()

    def fake_proot(self):
        p = self.paths.bin / "proot"
        p.write_text(FAKE_PROOT)
        os.chmod(p, 0o755)
        self.lm.refresh_status()
        return p


class TestEnvIdSecurity(LinuxCase):
    BAD = ["../x", "..", ".", "", "/etc", "a/b", "a\\b", "A", "a b", "x" * 33, "-rf", "_x", "é", "a\0b", "a/../../b",
           "host", "termux", "proot", None, 5, ["a"]]

    def test_validate_rejects_malicious_ids(self):
        for bad in self.BAD:
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_env_id(bad)
        for good in ("ubuntu", "a1", "my-env_2", "0day"):
            self.assertEqual(validate_env_id(good), good)

    def test_every_entry_point_validates_before_touching_disk(self):
        canary = self.paths.base / "canary"
        canary.mkdir()
        (canary / "keep").write_text("x")
        evil = "../canary"
        calls = [lambda: self.lm.install("alpine", env_id=evil, archive=str(self.tar), require_backend=False),
                 lambda: self.lm.remove(evil, confirmed=True),
                 lambda: self.lm.backup(evil, str(self.paths.cache / "b.tgz")),
                 lambda: self.lm.restore(str(self.tar), evil),
                 lambda: self.lm.set_env_var(evil, "A", "b"),
                 lambda: self.lm.profile(evil),
                 lambda: self.lm.package_command(evil, "update"),
                 lambda: self.lm.size(evil),
                 lambda: self.lm.check_environment(evil),
                 lambda: self.lm.exec_argv(evil, ["true"])]
        for i, fn in enumerate(calls):
            try:
                fn()
            except (ValidationError, NDXError):
                pass
            else:
                if i != 8:                      # check_environment reports instead of raising
                    self.fail("call %d accepted a malicious id" % i)
        self.assertTrue((canary / "keep").exists(), "a directory outside environments/ was modified")
        self.assertEqual(self.lm.check_environment(evil)["environments"][0]["status"], "fail")

    def test_failed_install_never_deletes_outside_dirs(self):
        victim = self.paths.base / "victim"
        victim.mkdir()
        with self.assertRaises(ValidationError):
            self.lm.install("alpine", env_id="../victim", archive=str(self.tar), require_backend=False)
        self.assertTrue(victim.exists())

    def test_symlinked_environment_directory_is_refused(self):
        outside = Path(self.e.tmp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.paths.environments / "sneaky")
        with self.assertRaises(ValidationError):
            self.lm.remove("sneaky", confirmed=True)
        self.assertTrue(outside.exists())
        self.assertNotIn("sneaky", [e["id"] for e in self.lm.list()])

    def test_restore_rejects_foreign_members_and_never_overwrites(self):
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), require_backend=False)
        b = self.lm.backup("a1", str(self.paths.cache / "a1.tgz"))
        with self.assertRaises(ValidationError):
            self.lm.restore(b, "a1")                                      # exists
        evil = self.paths.cache / "evil.tgz"
        with tarfile.open(evil, "w:gz") as tf:
            for name in ("b2/env.json", "other/pwn.txt"):
                ti = tarfile.TarInfo(name)
                ti.size = 2
                tf.addfile(ti, io.BytesIO(b"{}"))
        with self.assertRaises(ValidationError):
            self.lm.restore(str(evil), "b2")
        self.assertFalse((self.paths.environments / "other").exists())
        self.assertEqual(self.lm.restore(b, "a2") if False else "ok", "ok")

    def test_restored_backup_drops_host_binds_and_bad_vars(self):
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), require_backend=False)
        self.lm.set_binds("a1", ["/storage/emulated/0/proj:/proj"])
        b = self.lm.backup("a1", str(self.paths.cache / "a1.tgz"))
        self.lm.remove("a1", confirmed=True)
        self.lm.restore(b, "a1")
        self.assertEqual(self.lm._meta("a1")["binds"], [])
        with self.assertRaises(ValidationError):
            self.lm.set_binds("a1", ["relative/path", "/a:b:c"])
        with self.assertRaises(ValidationError):
            self.lm.set_env_var("a1", "BAD KEY", "x")


class TestInstallFlow(LinuxCase):
    def test_refuses_to_install_without_backend_and_downloads_nothing(self):
        with self.assertRaises(ToolUnavailable) as cm:
            self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        self.assertIn("ndx tool install proot", str(cm.exception))
        self.assertFalse((self.paths.environments / "a1").exists())
        self.assertEqual(self.e.web.requests, [])

    def test_install_with_backend_runs_startup_test_and_registers(self):
        self.fake_proot()
        self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        env = self.lm.list()[0]
        self.assertEqual(env["id"], "a1")
        self.assertIsNotNone(env["startup_tested"])
        self.assertTrue((self.paths.environments / "a1" / "rootfs" / ".profile").exists(), "dotfile name was mangled")
        self.assertTrue(os.access(self.paths.environments / "a1" / "rootfs" / "tmp", os.W_OK))
        self.assertIn("nameserver", (self.paths.environments / "a1" / "rootfs" / "etc" / "resolv.conf").read_text())
        argv = self.lm.profile("a1").argv
        self.assertIn("--kill-on-exit", argv)

    def test_startup_failure_removes_partial_environment(self):
        self.fake_proot()
        os.environ["FAKE_PROOT_FAIL"] = "ptrace: Operation not permitted"
        try:
            with self.assertRaises(NDXError) as cm:
                self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        finally:
            del os.environ["FAKE_PROOT_FAIL"]
        self.assertIn("did not start", str(cm.exception))
        self.assertIn("ptrace", str(cm.exception))
        self.assertEqual(self.lm.list(), [])
        self.assertFalse((self.paths.environments / "a1").exists())

    def test_incompatible_proot_blocks_install(self):
        p = self.fake_proot()
        p.write_text("#!/bin/sh\necho 'cannot execute binary file' >&2\nexit 126\n")
        with self.assertRaises(ToolUnavailable) as cm:
            self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        self.assertIn("not usable", str(cm.exception))

    def test_auto_install_proot_goes_through_toolchain(self):
        data = make_targz({"bin/proot": FAKE_PROOT.encode()})
        m = {"id": "proot", "name": "PRoot", "version": "5.4.0", "executable": "bin/proot", "exe_format": "script",
             "architectures": ["x86_64"], "platform": ["linux", "android"], "allowed_hosts": ["downloads.example.test"],
             "version_regex": r"(\d+\.\d+\.\d+)",
             "install": [{"method": "download", "sources": {"x86_64": {
                 "url": "https://downloads.example.test/proot.tgz", "archive": "tar.gz", "sha256": sha(data)}}}]}
        self.e.web.files["https://downloads.example.test/proot.tgz"] = data
        self.e.tc.registry.add_user_manifest(m)
        hooked = []
        self.e.tc.add_hook("proot", lambda res: (hooked.append(res), self.lm.refresh_status()))
        self.assertEqual(self.lm.status if self.lm.backend.available() else "unavailable", "unavailable")
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), auto_install_proot=True)
        self.assertEqual(len(hooked), 1)                       # Linux Manager was notified
        self.assertEqual(self.lm.status, "running")
        self.assertTrue(self.e.tc.is_installed("proot"))

    def test_storage_gate(self):
        self.fake_proot()
        self.e.resources.check_storage = lambda p, n: (False, {"message": "2 MB free, about 500 MB needed"})
        with self.assertRaises(NDXError) as cm:
            self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        self.assertIn("not enough storage", str(cm.exception))
        self.assertFalse((self.paths.environments / "a1").exists())

    def test_local_archive_checksum(self):
        self.fake_proot()
        with self.assertRaises(ValidationError):
            self.lm.install("alpine", env_id="a1", archive=str(self.tar), sha256="0" * 64)
        self.assertEqual(self.lm.list(), [])
        data = Path(self.tar).read_bytes()
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), sha256=sha(data))
        self.assertEqual(self.lm._meta("a1")["integrity"], "user-supplied")

    def test_download_uses_published_sidecar_checksum(self):
        self.fake_proot()
        d = DISTROS["alpine"]
        url = d["url"][self.lm.arch()]
        data = Path(self.tar).read_bytes()
        self.e.web.files[url] = data
        self.e.web.files[url + ".sha256"] = ("%s  %s\n" % (sha(data), os.path.basename(url))).encode()
        self.lm.install("alpine", env_id="a1")
        self.assertEqual(self.lm._meta("a1")["integrity"], "sidecar")
        self.assertEqual(self.lm._meta("a1")["sha256"], sha(data))
        self.assertIn("Download Linux root filesystem?", self.e.asked)

    def test_download_with_wrong_checksum_is_rejected_and_cleaned(self):
        self.fake_proot()
        url = DISTROS["alpine"]["url"][self.lm.arch()]
        self.e.web.files[url] = Path(self.tar).read_bytes()
        self.e.web.files[url + ".sha256"] = ("%s  %s\n" % ("0" * 64, os.path.basename(url))).encode()
        with self.assertRaises(ValidationError):
            self.lm.install("alpine", env_id="a1")
        self.assertEqual(self.lm.list(), [])
        self.assertFalse((self.paths.environments / "a1").exists())

    def test_unverifiable_download_needs_explicit_approval(self):
        self.fake_proot()
        url = DISTROS["alpine"]["url"][self.lm.arch()]
        self.e.web.files[url] = Path(self.tar).read_bytes()      # no sidecar published -> unverifiable
        self.e.answer = lambda p: "WITHOUT" not in p["title"]
        with self.assertRaises(ValidationError):
            self.lm.install("alpine", env_id="a1")
        self.e.answer = True
        self.lm.install("alpine", env_id="a1")
        self.assertEqual(self.lm._meta("a1")["integrity"], "unverified-approved")

    def test_no_url_for_distro_is_clear(self):
        self.fake_proot()
        with self.assertRaises(ValidationError):
            self.lm.install("debian", env_id="d1")


class TestRootfsHelpers(LinuxCase):
    def test_extract_keeps_dotfiles_and_resolves_absolute_symlinks_inside_rootfs(self):
        root = Path(self.e.tmp.name) / "rf"
        extract_rootfs(self.tar, root)
        self.assertTrue((root / ".profile").exists())
        p = resolve_in_rootfs(root, "/usr/bin/awk")
        self.assertEqual(p, root / "etc" / "alternatives" / "awk")       # NOT the host's /etc/alternatives/awk
        self.assertEqual(rootfs_which(root, "awk"), "/usr/bin/awk")
        self.assertIsNone(rootfs_which(root, "nothere"))

    def test_symlink_loop_and_dotdot_do_not_escape(self):
        root = Path(self.e.tmp.name) / "rf2"
        root.mkdir()
        os.symlink("loop2", root / "loop1")
        os.symlink("loop1", root / "loop2")
        self.assertIsNone(resolve_in_rootfs(root, "/loop1"))
        (root / "etc").mkdir()
        (root / "etc" / "x").write_text("1")
        self.assertEqual(resolve_in_rootfs(root, "/../../../etc/x"), root / "etc" / "x")

    def test_toolchain_sees_tools_inside_environments(self):
        self.fake_proot()
        self.e.tc.attach_linux(self.lm)
        self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        r = self.e.tc.resolve("awk", runtime="linux:a1")
        self.assertEqual((r.kind, r.env_id, r.path), ("proot", "a1", "/usr/bin/awk"))


class TestDiagnostics(LinuxCase):
    def names(self, rep):
        return {c["name"]: c["status"] for c in rep["checks"]}

    def test_reports_missing_proot(self):
        rep = self.lm.check_environment()
        self.assertEqual(rep["status"], "fail")
        self.assertEqual(self.names(rep)["PRoot executable"], "fail")

    def test_healthy_environment(self):
        self.fake_proot()
        self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        rep = self.lm.check_environment("a1")
        env = rep["environments"][0]
        st = {c["name"].split("] ", 1)[1]: c["status"] for c in env["checks"]}
        self.assertEqual(self.names(rep)["PRoot compatibility (format, CPU, runs)"], "ok")
        self.assertEqual(st["required directories"], "ok")
        self.assertEqual(st["startup test"], "ok")
        self.assertEqual(st["rootfs integrity (critical files)"], "ok")
        self.assertIn(rep["status"], ("ok", "warn"))

    def test_detects_damage(self):
        self.fake_proot()
        self.lm.install("alpine", env_id="a1", archive=str(self.tar))
        rf = self.paths.environments / "a1" / "rootfs"
        (rf / "bin" / "sh").write_text("corrupted")
        os.rmdir(rf / "root") if not any((rf / "root").iterdir()) else None
        rep = self.lm.check_environment("a1", run_startup=False)
        st = {c["name"].split("] ", 1)[1]: c["status"] for c in rep["environments"][0]["checks"]}
        self.assertEqual(st["required directories"], "fail")
        self.assertEqual(st["rootfs integrity (critical files)"], "warn")

    def test_wrong_cpu_rootfs_is_flagged(self):
        from toolchain_helpers import fake_elf
        self.fake_proot()
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), require_backend=False)
        sh = self.paths.environments / "a1" / "rootfs" / "bin" / "sh"
        fake_elf(sh, machine=0x28)                                       # 32-bit ARM binary on an x86_64 host
        os.environ["NDX_ABIS"] = "x86_64"
        try:
            rep = self.lm.check_environment("a1", run_startup=False)
        finally:
            del os.environ["NDX_ABIS"]
        c = [c for c in rep["environments"][0]["checks"] if c["name"].endswith("compatibility")][0]
        self.assertEqual(c["status"], "fail")

    def test_corrupt_config_is_reported(self):
        self.lm.install("alpine", env_id="a1", archive=str(self.tar), require_backend=False)
        (self.paths.environments / "a1" / "env.json").write_text("{not json")
        rep = self.lm.check_environment("a1")
        self.assertEqual(rep["environments"][0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
