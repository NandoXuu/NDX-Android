"""Filesystem layout for NDX.

On Android, mutable user data lives in the app-specific external files directory
(Android/data/<package>/files), which is available without broad storage access.
The packaged Python modules remain in the APK/app directory and are never moved.
"""
import os
import shutil
from pathlib import Path


def is_android():
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ or "ANDROID_APP_PATH" in os.environ


def _android_external_files_dir():
    """Return Context.getExternalFilesDir(None), or None when unavailable."""
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        directory = activity.getExternalFilesDir(None)
        if directory is not None:
            value = str(directory.getAbsolutePath())
            if value:
                return Path(value)
    except Exception:
        pass
    # Some python-for-android/Kivy versions expose the same location here.
    try:
        from kivy.app import App
        app = App.get_running_app()
        value = getattr(app, "user_data_dir", None) if app is not None else None
        if value:
            return Path(value)
    except Exception:
        pass
    return None


def detect_base_dir():
    override = os.environ.get("NDX_HOME")
    if override:
        return Path(override).expanduser()
    if is_android():
        external = _android_external_files_dir()
        if external is not None:
            target = external / "ndx_home"
            # Preserve existing installations when moving from the old private path.
            private = os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH")
            legacy = Path(private) / "ndx_home" if private else None
            if legacy and legacy.is_dir() and not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(str(legacy), str(target), dirs_exist_ok=True)
                except OSError:
                    # A full/temporarily unavailable external volume must not prevent startup.
                    return legacy
            return target
        # App-specific internal storage fallback; no broad storage permission required.
        priv = os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH")
        if priv:
            return Path(priv) / "ndx_home"
    return Path.home() / ".ndx"


class Paths:
    def __init__(self, base=None, assets=None):
        self.base = Path(base) if base else detect_base_dir()
        self.projects = self.base / "projects"
        self.plugins = self.base / "plugins"
        self.environments = self.base / "environments"
        self.cache = self.base / "cache"
        self.data = self.base / "data"
        self.logs = self.base / "logs"
        self.bin = self.base / "bin"
        self.models = self.base / "models"
        self.assets = Path(assets) if assets else Path(__file__).resolve().parents[2] / "assets"

    def ensure(self):
        for p in (self.base, self.projects, self.plugins, self.environments, self.cache,
                  self.data, self.logs, self.bin, self.models):
            p.mkdir(parents=True, exist_ok=True)
        return self
