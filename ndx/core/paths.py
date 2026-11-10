"""Filesystem layout. App data is kept apart from user projects and plugins."""
import os
from pathlib import Path


def is_android():
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ


def detect_base_dir():
    env = os.environ.get("NDX_HOME")
    if env:
        return Path(env)
    if is_android():
        # app-private storage: works with scoped storage, needs no permission
        priv = os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH")
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
