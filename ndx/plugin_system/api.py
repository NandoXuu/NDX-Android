"""Plugin SDK: the only surface plugins get. Every call is capability-checked.

Remember: this is an API boundary, not an OS sandbox (see security/permissions)."""
from ..language_support.model import Language


class PluginBase:
    """Subclass this in your plugin's entry module (class name set in [entry].class)."""

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass


class PluginContext:
    def __init__(self, plugin_id, core):
        self.plugin_id = plugin_id
        self._core = core
        self._perm = core.services.require("permissions")
        self._tokens, self.terminal_commands, self.panels = [], {}, []

    def _need(self, cap):
        self._perm.require(self.plugin_id, cap)

    def log(self, msg):
        import logging
        logging.getLogger("ndx.plugin." + self.plugin_id).info(msg)

    def subscribe(self, pattern, handler):
        self._need("event.subscribe")
        self._tokens.append(self._core.bus.subscribe(pattern, handler))

    def publish(self, name, payload=None):
        self._need("event.publish")
        self._core.bus.publish("plugin." + self.plugin_id + "." + name, payload, self.plugin_id)

    def get_setting(self, key, default=None):
        self._need("config.read")
        return self._core.config.get(key, default)

    def register_terminal_command(self, name, fn):
        """fn(args: list[str], out: callable) -> int"""
        self._need("terminal.command")
        self._core.services.require("console").register_command(name, fn, owner=self.plugin_id)
        self.terminal_commands[name] = fn

    def register_language(self, **fields):
        self._need("language.register")
        self._core.services.require("languages").register(
            Language(source="plugin:" + self.plugin_id, **fields))

    def run_tool(self, tool, args=(), environment="host", **kw):
        """Run an external tool (argument array). Needs the process.spawn capability."""
        self._need("process.spawn")
        return self._core.services.require("tools").run(tool, args, environment, caller="plugin:" + self.plugin_id, **kw)

    def add_panel(self, title, text_provider):
        """Adds a read-only info panel (text) to the Packages > Plugins screen."""
        self._need("ui.panel")
        self.panels.append((title, text_provider))

    def dispose(self):
        for t in self._tokens:
            self._core.bus.unsubscribe(t)
        reg = self._core.services.get("console")
        if reg:
            reg.unregister_owner(self.plugin_id)
