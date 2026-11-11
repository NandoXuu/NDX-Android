import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ndx.python_runtime.embedded import EmbeddedRunner, KIVY_ERROR, looks_like_kivy
from ndx.toolchain_manager.catalog import CATALOG, PROFILES


class CatalogTests(unittest.TestCase):
    def test_proot_loader_variables_point_at_bundled_loaders(self):
        proot = next(t for t in CATALOG if t["id"] == "proot")
        self.assertEqual(proot["env"]["PROOT_LOADER"], "{dir}/libproot-loader.so")
        self.assertEqual(proot["env"]["PROOT_LOADER32"], "{dir}/libproot-loader32.so")

    def test_gcc_and_jdk_are_installable_through_every_package_manager(self):
        by_id = {t["id"]: t for t in CATALOG}
        for tid in ("gcc", "jdk"):
            self.assertIn(tid, by_id)
            pk = next(i for i in by_id[tid]["install"] if i["method"] == "linux_package")["packages"]
            self.assertEqual(set(pk), {"apt", "apk", "pacman"})

    def test_every_profile_tool_exists_in_catalog(self):
        known = {t["id"] for t in CATALOG}
        for name, profile in PROFILES.items():
            self.assertEqual([t for t in profile["tools"] if t not in known], [], name)


class KivyThreadGuardTests(unittest.TestCase):
    def test_detects_kivy_imports_only_at_line_start(self):
        self.assertTrue(looks_like_kivy("from kivy.app import App\n"))
        self.assertTrue(looks_like_kivy("import kivymd.app\n"))
        self.assertFalse(looks_like_kivy("print('from kivy import x')\n"))

    def test_runner_refuses_kivy_script_without_executing_it(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "app.py")
            marker = os.path.join(d, "ran")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("from kivy.app import App\nopen(%r, 'w').close()\n" % marker)
            res = EmbeddedRunner().run(script, [], d, {}, None, None, None)
            self.assertEqual(res["returncode"], 1)
            self.assertEqual(res["error"], KIVY_ERROR)
            self.assertIn("Kivy", res["stderr"])
            self.assertFalse(os.path.exists(marker))

    def test_runner_still_runs_plain_python(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "plain.py")
            marker = os.path.join(d, "ran")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("open(%r, 'w').close()\n" % marker)
            res = EmbeddedRunner().run(script, [], d, {}, None, None, None)
            self.assertEqual(res["returncode"], 0)
            self.assertTrue(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
