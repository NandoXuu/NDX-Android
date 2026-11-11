"""Static checks of the build configuration. These do NOT build an APK and prove nothing about the APK itself."""
import ast
import configparser
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = set(sys.stdlib_module_names)
# import name -> requirements name
IMPORT_TO_DIST = {"kivy": "kivy", "kivymd": "kivymd", "PIL": "pillow", "pygments": "pygments", "jnius": "pyjnius",
                  "plyer": "plyer", "packaging": "packaging", "certifi": "certifi",
                  "android": "pyjnius"}      # `android` is provided by python-for-android itself


def spec():
    cp = configparser.RawConfigParser(strict=False)
    cp.optionxform = str
    cp.read(ROOT / "buildozer.spec")
    return cp["app"]


def req_names(path):
    names = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            names.append(re.split(r"[=<>]", line)[0].strip().lower())
    return names


def top_level_imports(path):
    """Imports executed at import time of the module (not inside functions/try-except ImportError guards)."""
    tree = ast.parse(Path(path).read_text())
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


class BuildConfigTests(unittest.TestCase):
    def test_kivymd_is_pinned_to_1_2_0_everywhere(self):
        for f in (ROOT / "buildozer.spec", ROOT / "requirements/android.txt", ROOT / "requirements/desktop.txt"):
            self.assertIn("kivymd==1.2.0", f.read_text(), f)

    def test_spec_requirements_equal_android_requirements(self):
        spec_reqs = [r.strip().lower() for r in spec()["requirements"].split(",")]
        self.assertEqual(sorted(spec_reqs), sorted(l.strip().lower() for l in
                                                  (ROOT / "requirements/android.txt").read_text().splitlines()
                                                  if l.strip() and not l.startswith("#")))

    def test_no_heavy_packages_in_apk(self):
        heavy = {"numpy", "scipy", "pandas", "torch", "tensorflow", "opencv", "opencv-python", "cryptography",
                 "requests", "llama-cpp-python", "onnxruntime"}
        self.assertFalse(heavy & set(req_names(ROOT / "requirements/android.txt")))

    def test_runtime_imports_are_covered_by_apk_requirements(self):
        have = set(req_names(ROOT / "requirements/android.txt"))
        bad = {}
        for f in (ROOT / "ndx").rglob("*.py"):
            for mod in top_level_imports(f):
                if mod in STDLIB or mod in ("ndx", "tests", "bindings", "service") or mod.startswith("_"):
                    continue
                dist = IMPORT_TO_DIST.get(mod)
                if dist is None or dist not in have:
                    bad.setdefault(mod, []).append(str(f.relative_to(ROOT)))
        self.assertEqual(bad, {}, "top-level imports of packages that are not in the APK requirements")

    def test_permissions_are_minimal_and_documented(self):
        perms = {p.strip() for p in spec()["android.permissions"].split(",")}
        self.assertEqual(perms, {"INTERNET", "ACCESS_NETWORK_STATE", "FOREGROUND_SERVICE", "POST_NOTIFICATIONS",
                                 "WAKE_LOCK", "com.termux.permission.RUN_COMMAND"})
        text = (ROOT / "docs/PERMISSIONS.md").read_text()
        for p in perms:
            self.assertIn(p, text, "%s is not documented in docs/PERMISSIONS.md" % p)
        for dangerous in ("MANAGE_EXTERNAL_STORAGE", "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE",
                          "REQUEST_INSTALL_PACKAGES", "SYSTEM_ALERT_WINDOW"):
            self.assertNotIn(dangerous, perms)

    def test_service_entry_points_at_an_existing_file(self):
        svc = spec()["services"]
        name, path, *_ = svc.split(":")
        self.assertTrue((ROOT / path).is_file(), path)
        self.assertIn("foreground", svc)

    def test_service_uses_supervisor(self):
        self.assertIn("Supervisor", (ROOT / "service/main.py").read_text())

    def test_source_excludes_keep_apk_small(self):
        ex = set(spec()["source.exclude_dirs"].split(","))
        for d in ("tests", "docs", "tools", "projects", "environments", "data", "logs", "cache", "bin"):
            self.assertIn(d, ex)

    def test_only_approved_proot_binaries_are_bundled(self):
        allowed = {
            "android-libs/arm64-v8a/libproot.so",
            "android-libs/arm64-v8a/libproot-userland.so",
            "android-libs/arm64-v8a/libproot-loader.so",
            "android-libs/arm64-v8a/libproot-loader32.so",
            "android-libs/armeabi-v7a/libproot.so",
            "android-libs/armeabi-v7a/libproot-userland.so",
            "android-libs/armeabi-v7a/libproot-loader.so",
        }

        found = {
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.suffix in (".so", ".exe", ".elf", ".a")
            and "tests" not in path.parts
        }

        self.assertEqual(found, allowed)

        for relative in allowed:
            path = ROOT / relative
            data = path.read_bytes()
            self.assertGreaterEqual(len(data), 20, relative)
            self.assertEqual(data[:4], bytes((127, 69, 76, 70)), relative)

            is_loader32 = "loader32" in relative
            expected_class = 1 if is_loader32 or "armeabi-v7a" in relative else 2
            expected_machine = 40 if expected_class == 1 else 183

            self.assertEqual(data[4], expected_class, relative)
            self.assertEqual(int.from_bytes(data[18:20], "little"), expected_machine, relative)

    def test_android_resources_listed_exist(self):
        listed = re.findall(r"%\(source\.dir\)s/(assets/android/res/[^:,]+):", spec()["android.add_resources"])
        self.assertGreater(len(listed), 10)
        self.assertEqual([p for p in listed if not (ROOT / p).is_file()], [])

    def test_api_and_arch_settings(self):
        s = spec()
        self.assertEqual(s["android.api"], "33")
        self.assertIn("arm64-v8a", s["android.archs"])

    def test_requirements_txt_points_to_desktop_set(self):
        self.assertIn("-r requirements/desktop.txt", (ROOT / "requirements.txt").read_text())


class VerifyApkToolTests(unittest.TestCase):
    def _apk(self, names):
        import tempfile
        import zipfile
        p = Path(tempfile.mkdtemp()) / "t.apk"
        with zipfile.ZipFile(p, "w") as z:
            for n in names:
                z.writestr(n, b"x")
        return str(p)

    def test_complete_apk_passes_and_incomplete_fails(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import verify_apk_resources as v
        names = ["resources.arsc", "res/mipmap-anydpi-v26/ndx_launcher.xml", "assets/DejaVuSansMono.ttf",
                 "res/drawable-nodpi/ndx_splash.png", "ndx/core/app_core.py"]
        for d in v.DENS:
            names += ["res/mipmap-%s/icon.png" % d, "res/drawable-%s/ic_stat_ndx.png" % d]
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(v.main([self._apk(names)]), 0)
            self.assertEqual(v.main([self._apk(names[:3])]), 1)


if __name__ == "__main__":
    unittest.main()
