import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from tests.helpers import CoreCase, EXAMPLES
from ndx.core import ValidationError, NDXError
from ndx.package_manager import validate_manifest, parse_manifest
from ndx.package_manager.github import parse_repo_url


def make_pkg(dest, pid="demo-plugin", version="1.0.0", extra="", code=False, deps=""):
    d = Path(dest) / (pid + "-" + version)
    d.mkdir(parents=True, exist_ok=True)
    toml = ('[plugin]\nid = "%s"\nname = "Demo"\nversion = "%s"\nauthor = "t"\ndescription = "d"\n'
            'ndx_version = ">=1.0.0"\n[plugin.type]\ncategory = "library"\n%s' % (pid, version, extra))
    if deps:
        toml += "[dependencies]\n" + deps
    if code:
        toml += '[entry]\nmodule = "main.py"\nclass = "Plugin"\n'
        (d / "main.py").write_text("from ndx.plugin_system import PluginBase\nclass Plugin(PluginBase):\n    pass\n")
    (d / "ndx-plugin.toml").write_text(toml)
    return d


def zip_dir(d, out):
    with zipfile.ZipFile(out, "w") as z:
        for f in Path(d).rglob("*"):
            z.write(f, f.relative_to(d.parent))
    return out


class TestManifest(unittest.TestCase):
    def test_validation(self):
        good = parse_manifest(open(os.path.join(EXAMPLES, "rust-support", "ndx-plugin.toml")).read())
        self.assertEqual(validate_manifest(good, "1.0.0")[1], [])
        bad = parse_manifest('[plugin]\nid = "X"\nversion = "x"\n')
        self.assertGreater(len(validate_manifest(bad, "1.0.0")[1]), 3)
        newer = parse_manifest(open(os.path.join(EXAMPLES, "rust-support", "ndx-plugin.toml")).read())
        newer["plugin"]["ndx_version"] = ">=9.0"
        self.assertTrue(any("requires NDX" in p for p in validate_manifest(newer, "1.0.0")[1]))
        unknown = parse_manifest('[plugin]\nid="aa"\nname="a"\nversion="1"\nauthor="a"\ndescription="d"\n'
                                 'ndx_version=">=1"\n[plugin.type]\ncategory="language"\n'
                                 '[permissions]\nrequest=["root.everything"]')
        self.assertTrue(any("unknown permission" in p for p in validate_manifest(unknown, "1.0.0")[1]))

    def test_github_url(self):
        self.assertEqual(parse_repo_url("https://github.com/a/b"), ("a", "b"))
        self.assertEqual(parse_repo_url("https://github.com/a/b.git"), ("a", "b"))
        for bad in ("http://github.com/a/b", "https://evil.com/a/b", "https://github.com/a"):
            with self.assertRaises(ValidationError):
                parse_repo_url(bad)


class TestInstall(CoreCase):
    def setUp(self):
        super().setUp()
        self.pm = self.core.services.get("packages")
        self.work = tempfile.mkdtemp()

    def test_install_declarative_registers_language_without_code(self):
        self.pm.install_path(os.path.join(EXAMPLES, "rust-support"))
        self.assertIn("rust-support", self.pm.list_installed())
        self.core.services.get("plugins").reload()
        langs = self.core.services.get("languages")
        self.assertEqual(langs.detect("x.rs").id, "rust-support")   # plugin registered (builtin 'rust' also exists)
        self.assertTrue(self.pm.verify("rust-support"))

    def test_zip_install_checksum(self):
        d = make_pkg(self.work)
        z = zip_dir(d, Path(self.work) / "p.zip")
        with self.assertRaises(ValidationError):
            self.pm.install_path(z, expected_sha256="0" * 64)
        digest = hashlib.sha256(z.read_bytes()).hexdigest()
        self.pm.install_path(z, expected_sha256=digest)
        with self.assertRaises(ValidationError):
            self.pm.install_path(z)                 # already installed

    def test_consent_denied_installs_nothing(self):
        CoreCase.consent_answer = False
        try:
            self.core.bus.subscribe("ui.consent.request", lambda e: None)
        finally:
            CoreCase.consent_answer = True
        self.core.bus._subs.clear()                  # no UI at all -> default deny
        with self.assertRaises(NDXError):
            self.pm.install_path(make_pkg(self.work))
        self.assertEqual(self.pm.list_installed(), {})

    def test_zip_slip_and_symlink_rejected(self):
        evil = Path(self.work) / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../../escape.txt", "x")
        with self.assertRaises(ValidationError):
            self.pm.install_path(evil)
        link = Path(self.work) / "link.zip"
        with zipfile.ZipFile(link, "w") as z:
            info = zipfile.ZipInfo("ln")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
        with self.assertRaises(ValidationError):
            self.pm.install_path(link)
        self.assertFalse(os.path.exists(os.path.join(self.work, "..", "escape.txt")))

    def test_missing_manifest_and_bad_entry(self):
        empty = Path(self.work) / "e"; empty.mkdir()
        with self.assertRaises(ValidationError):
            self.pm.install_path(empty)
        d = make_pkg(self.work, "code-plugin", code=True)
        (d / "main.py").unlink()
        with self.assertRaises(ValidationError):
            self.pm.install_path(d)

    def test_dependencies_update_rollback_uninstall(self):
        self.pm.install_path(make_pkg(self.work, "base-lib", "1.0.0"))
        with self.assertRaises(ValidationError):
            self.pm.install_path(make_pkg(self.work, "app-x", deps='missing-lib = ">=1"\n'))
        self.pm.install_path(make_pkg(self.work, "app-x", deps='base-lib = ">=1.0"\n'))
        with self.assertRaises(NDXError):
            self.pm.uninstall("base-lib")               # required by app-x
        self.pm.install_path(make_pkg(self.work, "base-lib", "1.1.0"), source="local2")
        self.assertEqual(self.pm.store.get("base-lib")["version"], "1.1.0")
        self.assertEqual(self.pm.rollback("base-lib"), "1.0.0")
        self.assertEqual(self.pm.store.get("base-lib")["version"], "1.0.0")
        self.pm.uninstall("app-x")
        self.pm.uninstall("base-lib")
        self.assertEqual(self.pm.list_installed(), {})
        self.assertFalse((self.core.paths.plugins / "base-lib").exists())

    def test_tamper_detected(self):
        self.pm.install_path(make_pkg(self.work))
        v = self.pm.store.version_dir("demo-plugin", "1.0.0")
        (v / "extra.txt").write_text("tampered")
        self.assertFalse(self.pm.verify("demo-plugin"))
        self.core.services.get("plugins").reload()
        self.assertIn("demo-plugin", self.core.services.get("plugins").errors)

    def test_install_log_written(self):
        self.pm.install_path(make_pkg(self.work))
        lines = (self.core.paths.data / "install_log.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(lines[0])["action"], "install")


class TestCodePlugin(CoreCase):
    def setUp(self):
        super().setUp()
        self.pm = self.core.services.get("packages")
        self.plugins = self.core.services.get("plugins")
        self.pm.install_path(os.path.join(EXAMPLES, "hello-commands"))
        self.out = []

    def run_cmd(self, line):
        return self.core.services.get("console").execute(line, self.out.append)

    def test_code_never_runs_until_approved(self):
        self.plugins.reload()
        self.assertEqual(self.run_cmd("hello"), 127)
        self.assertIn("hello-commands", self.plugins.errors)

    def test_setting_must_also_allow_code(self):
        self.plugins.approve_code("hello-commands", ["terminal.command", "event.subscribe"])
        self.assertEqual(self.run_cmd("hello"), 127)         # settings still forbid plugin code

    def test_approved_code_runs_with_capabilities(self):
        self.core.config.set("security.allow_plugin_code", True)
        self.plugins.approve_code("hello-commands", ["terminal.command", "event.subscribe"])
        self.assertEqual(self.run_cmd("hello Ada"), 0)
        self.assertIn("Hello Ada", "".join(self.out))
        self.core.services.get("projects").create("P1", "empty")
        self.out.clear()
        self.run_cmd("hello")
        self.assertIn("created this session: 1", "".join(self.out))
        self.plugins.unload("hello-commands")
        self.assertEqual(self.run_cmd("hello"), 127)

    def test_capability_denied_without_grant(self):
        from ndx.plugin_system import PluginContext
        ctx = PluginContext("hello-commands", self.core)
        with self.assertRaises(Exception):
            ctx.register_terminal_command("x", lambda a, o: 0)


class TestCli(CoreCase):
    def test_cli(self):
        out = []
        con = self.core.services.get("console")
        self.assertEqual(con.execute("ndx list", out.append), 0)
        self.assertEqual(con.execute("ndx install " + os.path.join(EXAMPLES, "solarized-theme"), out.append), 0)
        self.assertEqual(con.execute("ndx info solarized-theme", out.append), 0)
        self.assertEqual(con.execute("ndx search solar --offline", out.append), 0)
        self.assertEqual(con.execute("ndx doctor", out.append), 0)
        self.assertEqual(con.execute("ndx uninstall solarized-theme", out.append), 0)
        self.assertEqual(con.execute("ndx bogus", out.append), 2)
        self.assertEqual(con.execute("ndx install /nonexistent", out.append) != 0, True)
        self.assertIn("solarized-theme", "".join(out))


if __name__ == "__main__":
    unittest.main()
