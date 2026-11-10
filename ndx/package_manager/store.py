"""Installed-plugin registry on disk, with install history for rollback."""
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path


def tree_hash(path):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            fp = Path(root) / f
            h.update(str(fp.relative_to(path)).encode())
            with open(fp, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


class PluginStore:
    def __init__(self, plugins_dir, data_dir):
        self.dir = Path(plugins_dir)
        self.index_path = Path(data_dir) / "installed_plugins.json"
        self.log_path = Path(data_dir) / "install_log.jsonl"
        self._lock = threading.RLock()
        self._index = {}
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.index_path, "r", encoding="utf-8") as fh:
                self._index = json.load(fh)
        except (OSError, ValueError):
            self._index = {}

    def _save(self):
        tmp = str(self.index_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._index, fh, indent=2)
        os.replace(tmp, self.index_path)

    def log(self, action, **kw):
        with self._lock, open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "action": action, **kw}) + "\n")

    def get(self, pid):
        return self._index.get(pid)

    def all(self):
        return dict(self._index)

    def version_dir(self, pid, version):
        return self.dir / pid / version

    def commit(self, pid, version, meta):
        with self._lock:
            entry = self._index.get(pid, {"history": [], "enabled": True, "code_approved": False})
            if entry.get("version") and entry["version"] != version:
                entry["history"] = (entry.get("history", []) + [entry["version"]])[-5:]
            entry.update(meta)
            entry["version"] = version
            self._index[pid] = entry
            self._save()
        self.log("install", id=pid, version=version, source=meta.get("source"))

    def set_flag(self, pid, key, value):
        with self._lock:
            self._index[pid][key] = value
            self._save()

    def previous_version(self, pid):
        e = self._index.get(pid)
        return e["history"][-1] if e and e.get("history") else None

    def rollback(self, pid):
        with self._lock:
            e = self._index.get(pid)
            if not e or not e.get("history"):
                return None
            prev = e["history"].pop()
            bad = e["version"]
            if not self.version_dir(pid, prev).exists():
                return None
            e["version"] = prev
            self._save()
        shutil.rmtree(self.version_dir(pid, bad), ignore_errors=True)
        self.log("rollback", id=pid, to=prev, removed=bad)
        return prev

    def remove(self, pid):
        with self._lock:
            self._index.pop(pid, None)
            self._save()
        shutil.rmtree(self.dir / pid, ignore_errors=True)
        self.log("uninstall", id=pid)
