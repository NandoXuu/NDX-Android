"""Capability grants for plugins, with trust levels and an audit trail.

NOTE: capability checks guard the NDX Plugin API. They are NOT an OS-level
sandbox: Python plugin code runs in-process, which is why code plugins need
explicit user approval (see plugin_system.loader)."""
import json
import threading

from ..core.errors import PermissionDenied

TRUST_LEVELS = ("untrusted", "user_approved", "verified", "builtin")

CAPABILITIES = {
    "event.subscribe": "Listen to NDX events",
    "event.publish": "Publish NDX events",
    "config.read": "Read NDX settings",
    "config.write": "Change NDX settings",
    "terminal.command": "Register terminal commands",
    "language.register": "Register language support",
    "compiler.register": "Register compilers/build tools",
    "ui.panel": "Add interface panels",
    "fs.read": "Read files in the active project",
    "fs.write": "Modify files in the active project",
    "net.client": "Make outgoing network requests",
    "net.server": "Open local network ports",
    "process.spawn": "Start external processes",
}
SENSITIVE = {"fs.write", "net.client", "net.server", "process.spawn", "config.write"}


class PermissionService:
    def __init__(self, store_path, audit=None):
        self.path = store_path
        self.audit = audit or (lambda *a, **k: None)
        self._lock = threading.RLock()
        self._grants = {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._grants = json.load(fh)
        except (OSError, ValueError):
            self._grants = {}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._grants, fh, indent=2)

    def trust(self, plugin_id):
        return self._grants.get(plugin_id, {}).get("trust", "untrusted")

    def set_trust(self, plugin_id, level):
        if level not in TRUST_LEVELS:
            raise ValueError("unknown trust level")
        with self._lock:
            self._grants.setdefault(plugin_id, {"granted": []})["trust"] = level
            self._save()
        self.audit("trust.set", plugin=plugin_id, level=level)

    def grant(self, plugin_id, caps):
        unknown = [c for c in caps if c not in CAPABILITIES]
        if unknown:
            raise ValueError("unknown capabilities: %s" % unknown)
        with self._lock:
            entry = self._grants.setdefault(plugin_id, {"trust": "untrusted", "granted": []})
            entry["granted"] = sorted(set(entry["granted"]) | set(caps))
            self._save()
        self.audit("permission.grant", plugin=plugin_id, caps=list(caps))

    def revoke_all(self, plugin_id):
        with self._lock:
            self._grants.pop(plugin_id, None)
            self._save()
        self.audit("permission.revoke_all", plugin=plugin_id)

    def granted(self, plugin_id):
        return list(self._grants.get(plugin_id, {}).get("granted", []))

    def check(self, plugin_id, cap):
        return cap in self._grants.get(plugin_id, {}).get("granted", [])

    def require(self, plugin_id, cap):
        if not self.check(plugin_id, cap):
            self.audit("permission.denied", plugin=plugin_id, cap=cap)
            raise PermissionDenied("plugin '%s' lacks capability '%s'" % (plugin_id, cap))
