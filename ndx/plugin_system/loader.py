"""Loads installed plugins.

- Declarative plugins ([language]/[compiler]/[theme] only) are registered with
  NO code execution.
- Plugins with [entry] code load only if: enabled + user granted their
  permissions + the 'code_approved' flag was set by an explicit user action
  + settings allow plugin code."""
import importlib.util
import logging

from ..core.registry import Module
from ..language_support.model import Language
from .api import PluginBase, PluginContext

log = logging.getLogger("ndx.plugins")


class PluginManager(Module):
    name = "plugins"
    title = "Plugin System"

    def __init__(self):
        self.loaded = {}      # id -> (manifest, instance|None, ctx|None)
        self.themes = {}
        self.errors = {}

    def start(self, core):
        self.core = core
        self.reload()

    def reload(self):
        for pid in list(self.loaded):
            self.unload(pid)
        pm = self.core.services.get("packages")
        for pid, entry in pm.list_installed().items():
            if entry.get("enabled", True):
                self.load(pid)

    def unload(self, pid):
        item = self.loaded.pop(pid, None)
        if not item:
            return
        manifest, inst, ctx = item
        try:
            if inst:
                inst.on_unload()
        except Exception:
            log.exception("plugin %s on_unload failed", pid)
        if ctx:
            ctx.dispose()
        self.core.services.require("languages").unregister_source("plugin:" + pid)
        self.themes.pop(pid, None)

    def approve_code(self, pid, perms):
        """Called only from an explicit user action in the UI."""
        pm = self.core.services.get("packages")
        self.core.services.require("permissions").grant(pid, perms)
        self.core.services.require("permissions").set_trust(pid, "user_approved")
        pm.store.set_flag(pid, "code_approved", True)
        self.core.logs.audit("plugin.code_approved", id=pid, perms=perms)
        self.load(pid)

    def load(self, pid):
        pm = self.core.services.get("packages")
        entry = pm.store.get(pid)
        root = pm.store.version_dir(pid, entry["version"])
        manifest, problems = pm.inspect_dir(root)
        if problems or not pm.verify(pid):
            self.errors[pid] = "failed validation or integrity check"
            return False
        self._declarative(manifest)
        inst = ctx = None
        if manifest.has_code:
            allowed = (entry.get("code_approved") and self.core.config.get("security.allow_plugin_code", False)
                       and self.core.services.require("permissions").trust(pid) in ("user_approved", "verified", "builtin"))
            if allowed:
                try:
                    inst, ctx = self._run_code(manifest, root)
                except Exception as exc:
                    log.exception("plugin %s failed", pid)
                    self.errors[pid] = "%s: %s" % (type(exc).__name__, exc)
            else:
                self.errors[pid] = "code not approved (declarative parts only)"
        self.loaded[pid] = (manifest, inst, ctx)
        return True

    def _declarative(self, m):
        langs = self.core.services.require("languages")
        if m.language:
            l = m.language
            comp = m.compiler or {}
            langs.register(Language(
                id=m.id, name=l.get("name", m.name), extensions=list(l.get("extensions", [])),
                lexer=l.get("lexer", "text"), comment=l.get("comment", "#"),
                keywords=list(l.get("keywords", [])),
                tools=[comp["command"]] if comp.get("command") else [],
                build=comp.get("build", ""), run=comp.get("run", ""), source="plugin:" + m.id))
        if m.theme:
            self.themes[m.id] = dict(m.theme)

    def _run_code(self, m, root):
        path = root / m.entry["module"]
        spec = importlib.util.spec_from_file_location("ndx_plugin_" + m.id.replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, m.entry.get("class", "Plugin"))
        if not (isinstance(cls, type) and issubclass(cls, PluginBase)):
            raise TypeError("entry class must subclass ndx.plugin_system.PluginBase")
        ctx = PluginContext(m.id, self.core)
        inst = cls()
        inst.on_load(ctx)
        return inst, ctx

    def stop(self):
        for pid in list(self.loaded):
            self.unload(pid)
        self.status = "stopped"
