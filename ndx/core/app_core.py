"""NDX Core: wires services and modules together. UI-independent."""
import logging

from .. import __version__
from .config import ConfigManager
from .consent import ConsentGateway
from .events import EventBus
from .logging_service import LoggingService
from .paths import Paths
from .registry import ModuleRegistry, ServiceRegistry
from .tasks import TaskManager

log = logging.getLogger("ndx.core")
_ACTIVE_CORE = None


def active():
    """The most recently created NDXCore (used by ndx.tools / ndx.terminal module-level APIs)."""
    return _ACTIVE_CORE


class NDXCore:
    version = __version__

    def __init__(self, base_dir=None, main_dispatcher=None, assets_dir=None):
        self.paths = Paths(base_dir, assets_dir).ensure()
        self.dispatch = main_dispatcher or (lambda fn: fn())
        self.bus = EventBus(self.dispatch)
        self.logs = LoggingService(self.paths.logs)
        self.config = ConfigManager(self.paths.data / "config.json", self.bus)
        self.tasks = TaskManager(self.bus, main_dispatcher=self.dispatch)
        self.services = ServiceRegistry()
        self.modules = ModuleRegistry(self)
        self.consent = ConsentGateway(self.bus, self.logs.audit)
        self._ipc = None
        self._build()
        global _ACTIVE_CORE
        _ACTIVE_CORE = self

    def _build(self):
        # imports are local so a broken optional module cannot stop the Core
        from ..security import PermissionService, SecretStore
        from ..resource_manager import ResourceMonitor, ResourceManager
        from ..language_support import LanguageRegistry, register_builtin
        from ..project_manager import ProjectManager
        from ..compiler_manager import RunService
        from ..shell_environment import ShellEnvironment
        from ..terminal import TerminalManager
        from ..terminal.bridge import TerminalBridge
        from ..package_manager import PackageManager
        from ..plugin_system import PluginManager
        from ..plugin_system.console import ConsoleService
        from ..linux_manager import LinuxManager
        from ..server_manager import ServerManager
        from ..ai_runtime import AIRuntimeManager
        from ..android_toolkit import AdbClient, LogcatMonitor, CrashLogStore
        from ..toolchain_manager import ToolchainManager, ToolRunner
        from ..termux_integration import TermuxIntegration

        p, s = self.paths, self.services
        s.register("bus", self.bus)
        s.register("config", self.config)
        s.register("tasks", self.tasks)
        s.register("consent", self.consent)
        s.register("audit", self.logs.audit)
        s.register("permissions", PermissionService(p.data / "permissions.json", self.logs.audit))
        s.register("secrets", SecretStore(p.data / "secrets.json"))
        monitor = s.register("monitor", ResourceMonitor(p.base))
        resources = s.register("resources", ResourceManager(self.config.get("servers.global_ram_budget_mb", 1024),
                                                            self.bus, monitor, p.base))
        reg = self.modules.register
        # The Toolchain Manager is created FIRST: every other module resolves executables through it.
        toolchain = reg(ToolchainManager(p, self.bus, self.config, self.logs.audit, self.consent, self.tasks, resources))
        termux = reg(TermuxIntegration(self.bus, self.config, self.logs.audit))
        toolchain.attach_termux(termux)
        langs = register_builtin(LanguageRegistry([p.bin]))
        s.register("languages", langs)
        s.register("adb", AdbClient([p.bin], toolchain=toolchain))
        s.register("crashlogs", CrashLogStore(p.data / "crashlogs"))
        s.register("logcat", LogcatMonitor(s.get("adb"), s.get("crashlogs")))

        projects = reg(ProjectManager(p.projects, p.data, self.bus, self.logs.audit))
        projects.git_services = {"toolchain": toolchain, "config": self.config, "secrets": s.get("secrets"),
                                 "audit": self.logs.audit}
        runner = reg(RunService(self.bus, self.tasks, self.logs.audit, self.consent))
        shell = reg(ShellEnvironment(p, self.config))
        shell.env_provider = lambda: toolchain.environment("host")
        reg(TerminalManager(shell, self.bus, self.config, self.logs.audit))
        reg(PackageManager(p, self.bus, self.consent, self.logs.audit))
        reg(ConsoleService())
        servers = reg(ServerManager(p.data, self.bus, resources, monitor, self.logs.audit, self.consent))
        linux = reg(LinuxManager(p, self.bus, self.logs.audit, self.consent, self.tasks, toolchain=toolchain,
                                 resources=resources))
        toolchain.attach_linux(linux)
        toolchain.add_hook("proot", linux.refresh_status)
        reg(AIRuntimeManager(p, servers, resources, self.bus))
        bridge = reg(TerminalBridge(p, toolchain, linux, termux, shell, resources, self.logs.audit, self.bus, self.config))
        bridge.script_env = self._script_env
        from ..python_runtime import PythonRuntime, EmbeddedPythonTarget
        pyrt = reg(PythonRuntime(toolchain, bridge, linux, termux, self.consent, self.config, self.logs.audit))
        runner.register_target(EmbeddedPythonTarget(pyrt))
        s.register("tools", ToolRunner(bridge, toolchain, s.get("permissions"), self.consent, self.logs.audit,
                                       self.config, p))
        reg(PluginManager())          # last: needs languages, packages, console
        for m in self.modules.all():
            s.register(m.name, m)
        s.register("project_manager", projects)
        # tools installed/removed later must become visible to everything that cached a scan
        self.bus.subscribe("toolchain.changed", lambda e: (shell.rescan(), linux.refresh_status()))

    # ---- authenticated IPC for scripts running in other processes (external Python / Lua / Node / Linux / Termux)
    def ensure_ipc(self):
        """Start (once) the loopback-only, token-authenticated IPC server and register it as service 'ipc'."""
        if self._ipc is None:
            from ..application_framework import IpcServer
            self._ipc = IpcServer(core=self, audit=lambda event, **kw: self.logs.audit(event, **kw)).start()
            self.services.register("ipc", self._ipc)
        return self._ipc

    def _script_env(self, label, ttl):
        """Per-launch credentials for a script: scope 'tools' only, short-lived, revoked when the launch ends."""
        ipc = self.ensure_ipc()
        token = ipc.issue_token(scopes=("tools",), ttl=ttl, label=label + "#" + str(id(object()) % 10 ** 6))
        return ({"NDX_IPC_PORT": str(ipc.port), "NDX_IPC_TOKEN": token, "NDX_IPC_HOST": "127.0.0.1"},
                lambda: ipc.revoke_token(token))

    def start(self):
        self.modules.start_all()
        self.bus.publish("core.started", {"version": self.version}, "core")
        return self

    def stop(self):
        self.bus.publish("core.stopping", {}, "core")
        self.consent.cancel_all()
        if self._ipc is not None:
            try:
                self._ipc.stop()
            except Exception:
                log.exception("ipc stop failed")
            self._ipc = None
        self.modules.stop_all()
        self.tasks.shutdown()
        global _ACTIVE_CORE
        if _ACTIVE_CORE is self:
            _ACTIVE_CORE = None
