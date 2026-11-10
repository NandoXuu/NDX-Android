import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ndx.core import paths
from ndx.terminal.pty_session import _inject_app_pythonpath


class AndroidPathTests(unittest.TestCase):
    def test_android_uses_app_specific_external_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "Android" / "data" / "org.ndx.ndx" / "files"
            with patch.dict(os.environ, {"ANDROID_ARGUMENT": "1"}, clear=True), \
                 patch.object(paths, "_android_external_files_dir", return_value=external):
                self.assertEqual(paths.detect_base_dir(), external / "ndx_home")

    def test_existing_private_home_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "private"
            external = Path(tmp) / "external"
            legacy = private / "ndx_home"
            (legacy / "projects").mkdir(parents=True)
            (legacy / "projects" / "keep.txt").write_text("preserve")
            with patch.dict(os.environ, {"ANDROID_ARGUMENT": "1", "ANDROID_PRIVATE": str(private)}, clear=True), \
                 patch.object(paths, "_android_external_files_dir", return_value=external):
                result = paths.detect_base_dir()
            self.assertEqual(result, external / "ndx_home")
            self.assertEqual((result / "projects" / "keep.txt").read_text(), "preserve")

    def test_ndx_home_override_has_priority(self):
        with patch.dict(os.environ, {"NDX_HOME": "/tmp/ndx-custom", "ANDROID_ARGUMENT": "1"}, clear=True):
            self.assertEqual(paths.detect_base_dir(), Path("/tmp/ndx-custom"))


class AndroidPtyEnvironmentTests(unittest.TestCase):
    def test_pythonpath_includes_bundled_ndx_root(self):
        env = {"ANDROID_ARGUMENT": "1", "PYTHONPATH": "/existing"}
        result = _inject_app_pythonpath(env)
        app_root = str(Path(__file__).resolve().parents[1])
        self.assertEqual(result["PYTHONPATH"].split(os.pathsep), [app_root, "/existing"])

    def test_pythonpath_is_not_duplicated(self):
        app_root = str(Path(__file__).resolve().parents[1])
        env = {"ANDROID_PRIVATE": "/private", "PYTHONPATH": app_root}
        _inject_app_pythonpath(env)
        self.assertEqual(env["PYTHONPATH"], app_root)


if __name__ == "__main__":
    unittest.main()
