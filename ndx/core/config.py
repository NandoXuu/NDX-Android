"""Persistent JSON configuration with dotted keys."""
import copy
import json
import os
import threading

DEFAULTS = {
    "ui": {"theme": "dark", "accent": "Teal", "font_scale": 1.0},
    "editor": {"font_size": 14, "tab_width": 4, "line_numbers": True, "autosave": False},
    "terminal": {"font_size": 12, "scrollback": 2000, "default_shell": ""},
    "packages": {"index_url": "", "github_api": True},
    "servers": {"default_host": "127.0.0.1", "global_ram_budget_mb": 1024},
    "security": {"allow_plugin_code": False},
    "session": {"open_files": [], "active_file": "", "last_project": ""},
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class ConfigManager:
    def __init__(self, path, bus=None, defaults=None):
        self.path = path
        self.bus = bus
        self._lock = threading.RLock()
        self._data = copy.deepcopy(defaults or DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                _merge(self._data, json.load(fh))
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            try:
                os.replace(self.path, str(self.path) + ".corrupt")
            except OSError:
                pass

    def get(self, key, default=None):
        with self._lock:
            node = self._data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key, value, save=True):
        with self._lock:
            node = self._data
            parts = key.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        if save:
            self.save()
        if self.bus:
            self.bus.publish("config.changed", {"key": key, "value": value}, "config")

    def save(self):
        with self._lock:
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._data)
