"""ToolRegistry: manifests (built-in + user) and the persisted install state."""
import json
import logging
import os
import threading
import time

from ..core.errors import ValidationError
from ..storage import atomic_write
from .catalog import builtin_manifests
from .manifest import validate_manifest

log = logging.getLogger("ndx.toolchain")

HOST = "host"
TERMUX = "termux"


def env_scope(env_id):
    return "env:" + env_id


class ToolRegistry:
    """Scopes of an installed tool: `host` (executable by NDX itself), `env:<id>` (inside a Linux/PRoot
    environment) and `termux` (inside Termux, reachable only through the integration)."""

    def __init__(self, manifests_dir, state_path):
        self.manifests_dir = manifests_dir
        self.state_path = state_path
        self._lock = threading.RLock()
        self._manifests = {}
        self.errors = {}        # file -> problem, for invalid user manifests
        self._state = {"schema": 1, "tools": {}}
        self.reload()

    # ---- manifests
    def reload(self):
        with self._lock:
            self._manifests = {m.id: m for m in builtin_manifests()}
            self.errors = {}
            if self.manifests_dir.is_dir():
                for f in sorted(self.manifests_dir.glob("*.json")):
                    try:
                        m = validate_manifest(json.loads(f.read_text("utf-8")), origin="user")
                        self._manifests[m.id] = m          # a user manifest overrides the built-in one
                    except (ValidationError, ValueError, OSError) as e:
                        self.errors[f.name] = "%s %s" % (e, getattr(e, "problems", ""))
                        log.warning("ignoring tool manifest %s: %s", f.name, e)
            try:
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    st = json.load(fh)
                if isinstance(st, dict) and isinstance(st.get("tools"), dict):
                    self._state = st
            except (OSError, ValueError):
                pass

    def get(self, tool_id):
        return self._manifests.get(tool_id)

    def require(self, tool_id):
        m = self._manifests.get(tool_id)
        if m is None:
            raise ValidationError("unknown tool: %s (see `ndx tool list`)" % tool_id)
        return m

    def all(self):
        return [self._manifests[k] for k in sorted(self._manifests)]

    def by_command(self, command):
        for m in self._manifests.values():
            if command in m.commands() or command == m.id:
                return m
        return None

    def add_user_manifest(self, data):
        m = validate_manifest(data, origin="user")
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.manifests_dir / (m.id + ".json"), json.dumps(data, indent=2, sort_keys=True))
        with self._lock:
            self._manifests[m.id] = m
        return m

    # ---- state
    def _save(self):
        atomic_write(self.state_path, json.dumps(self._state, indent=2, sort_keys=True))

    def installs(self, tool_id):
        with self._lock:
            return json.loads(json.dumps(self._state["tools"].get(tool_id, {})))

    def install_record(self, tool_id, scope=HOST):
        return self.installs(tool_id).get(scope)

    def set_install(self, tool_id, scope, record):
        with self._lock:
            self._state["tools"].setdefault(tool_id, {})[scope] = record
            self._save()

    def drop_install(self, tool_id, scope=None):
        with self._lock:
            t = self._state["tools"].get(tool_id, {})
            if scope is None:
                t.clear()
            else:
                t.pop(scope, None)
            if not t:
                self._state["tools"].pop(tool_id, None)
            self._save()

    def installed_ids(self):
        with self._lock:
            return sorted(self._state["tools"])

    def dependents(self, tool_id, scope=HOST):
        """Installed tools (in `scope`) whose manifest requires `tool_id`."""
        out = []
        for tid in self.installed_ids():
            m = self.get(tid)
            if m and tool_id in m.requires and scope in self.installs(tid):
                out.append(tid)
        return out

    @staticmethod
    def now():
        return time.time()
