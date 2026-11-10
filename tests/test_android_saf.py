import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ndx.android_storage import AndroidSAF, SAFUnavailable


class AndroidSAFTests(unittest.TestCase):
    def test_import_does_not_require_android_runtime(self):
        # The package can be imported by desktop tools and unit tests.
        self.assertTrue(callable(AndroidSAF))

    def test_android_runtime_is_required_to_construct(self):
        with self.assertRaises(SAFUnavailable):
            AndroidSAF()

    def test_manifest_declares_only_termux_package_visibility(self):
        root = ET.fromstring("<manifest xmlns:android='http://schemas.android.com/apk/res/android'>"
                             + (ROOT / "android" / "extra_manifest.xml").read_text()
                             + "</manifest>")
        queries = root.find("queries")
        self.assertIsNotNone(queries)
        packages = [e.attrib["{http://schemas.android.com/apk/res/android}name"]
                    for e in queries.findall("package")]
        self.assertEqual(packages, ["com.termux"])

    def test_buildozer_includes_extra_manifest_and_no_broad_storage_permission(self):
        spec = (ROOT / "buildozer.spec").read_text()
        self.assertIn("android.extra_manifest_xml = ./android/extra_manifest.xml", spec)
        permissions = next(line for line in spec.splitlines() if line.startswith("android.permissions"))
        self.assertNotIn("READ_EXTERNAL_STORAGE", permissions)
        self.assertNotIn("WRITE_EXTERNAL_STORAGE", permissions)
        self.assertNotIn("MANAGE_EXTERNAL_STORAGE", permissions)

    def test_saf_uri_validation_rejects_filesystem_paths(self):
        # Exercise URI validation without Android by constructing an uninitialized instance.
        instance = object.__new__(AndroidSAF)
        with self.assertRaises(ValueError):
            instance._uri("/storage/emulated/0/file.txt")
        with self.assertRaises(ValueError):
            instance._uri("file:///storage/emulated/0/file.txt")


if __name__ == "__main__":
    unittest.main()
