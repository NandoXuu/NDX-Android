"""Script-facing facade: `import ndx.tools; ndx.tools.run("git", ["status"])`.

Works inside the NDX process (plugins / embedded Python). Scripts running in OTHER processes (external Python,
Lua, Node, Linux, Termux) use the authenticated IPC bindings in bindings/ instead."""
from .core import active


def _runner():
    core = active()
    if core is None:
        raise RuntimeError("NDX core is not running")
    return core.services.require("tools")


def run(tool, args=(), environment="host", **kw):
    """Run a tool (argument array, never a shell string). Returns a ToolResult (.ok .returncode .stdout .stderr)."""
    return _runner().run(tool, args, environment, caller=kw.pop("caller", "script"), **kw)


def resolve(tool, environment="host"):
    return _runner().resolve_info(tool, environment)


def list_tools():
    return _runner().list_tools()
