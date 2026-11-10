"""Module registry and service registry."""
import logging

log = logging.getLogger("ndx.registry")


class Module:
    """Base class of every NDX core module. A module that fails to start is
    marked `failed`; the rest of the app keeps running."""
    name = "module"
    title = "Module"
    status = "stopped"      # stopped | running | failed | unavailable
    error = ""

    def start(self, core):
        self.status = "running"

    def stop(self):
        self.status = "stopped"

    def diagnostics(self):
        return {"status": self.status, "error": self.error}


class ServiceRegistry:
    def __init__(self):
        self._s = {}

    def register(self, name, service):
        self._s[name] = service
        return service

    def get(self, name, default=None):
        return self._s.get(name, default)

    def require(self, name):
        if name not in self._s:
            raise KeyError("service not registered: %s" % name)
        return self._s[name]

    def has(self, name):
        return name in self._s

    def names(self):
        return sorted(self._s)


class ModuleRegistry:
    def __init__(self, core=None):
        self._mods = {}
        self._order = []
        self.core = core

    def register(self, module):
        if module.name in self._mods:
            raise ValueError("duplicate module: " + module.name)
        self._mods[module.name] = module
        self._order.append(module.name)
        return module

    def get(self, name):
        return self._mods.get(name)

    def all(self):
        return [self._mods[n] for n in self._order]

    def start_all(self):
        for m in self.all():
            try:
                m.start(self.core)
                if m.status == "stopped":
                    m.status = "running"
                log.info("module %s: %s", m.name, m.status)
            except Exception as exc:
                m.status = "failed"
                m.error = "%s: %s" % (type(exc).__name__, exc)
                log.exception("module %s failed to start", m.name)

    def stop_all(self):
        for m in reversed(self.all()):
            try:
                m.stop()
            except Exception:
                log.exception("module %s failed to stop", m.name)
