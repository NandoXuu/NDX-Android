"""Unified platform services for projects: files, key-value storage, SQLite,
networking, notifications, lifecycle. Adapters keep platform code out of callers."""
import json
import os
import sqlite3
import urllib.request
from pathlib import Path


class PlatformServices:
    def __init__(self, app_dir, notifier=None):
        self.root = Path(app_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._notifier = notifier or (lambda title, text: None)
        self._hooks = {"start": [], "pause": [], "resume": [], "stop": []}
        self.db_path = self.root / "app.db"

    # files (sandboxed to the app dir)
    def _p(self, rel):
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise PermissionError("path outside application storage")
        return p

    def read_file(self, rel):
        return self._p(rel).read_bytes()

    def write_file(self, rel, data):
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))

    def list_files(self, rel="."):
        return sorted(os.listdir(self._p(rel)))

    # key-value
    def kv_set(self, key, value):
        with self.db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value)))

    def kv_get(self, key, default=None):
        with self.db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # database
    def db(self):
        return sqlite3.connect(self.db_path)

    # network
    def http_get(self, url, timeout=15):
        if not url.startswith(("http://", "https://")):
            raise ValueError("only http(s) URLs")
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(10 * 1024 * 1024)

    # notifications / lifecycle
    def notify(self, title, text):
        self._notifier(title, text)

    def on_lifecycle(self, phase, fn):
        self._hooks[phase].append(fn)

    def fire(self, phase):
        for fn in self._hooks[phase]:
            fn()
