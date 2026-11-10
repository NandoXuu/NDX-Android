"""The NDX console: `ndx ...` package commands plus plugin-registered commands."""
from ..core.registry import Module
from ..package_manager.cli import NdxCli


class ConsoleService(Module):
    name = "console"
    title = "NDX Console"

    def __init__(self):
        self.commands = {}
        self.owners = {}

    def start(self, core):
        self.core = core
        self.cli = NdxCli(core)

    def register_command(self, name, fn, owner="core"):
        if name in ("ndx", "help", "clear") or name in self.commands:
            raise ValueError("command name not available: " + name)
        self.commands[name] = fn
        self.owners[name] = owner

    def unregister_owner(self, owner):
        for n in [n for n, o in self.owners.items() if o == owner]:
            self.commands.pop(n, None)
            self.owners.pop(n, None)

    def execute(self, line, out):
        import shlex
        line = line.strip()
        if not line:
            return 0
        if line == "help":
            out("commands: ndx, help, clear" + "".join(", " + c for c in sorted(self.commands)) + "\n")
            return 0
        head = line.split(None, 1)[0]
        if head == "ndx":
            return self.cli.run(line, out)
        if head in self.commands:
            try:
                return self.commands[head](shlex.split(line)[1:], out) or 0
            except Exception as exc:
                out("error: %s\n" % exc)
                return 1
        out("unknown command: %s (type 'help')\n" % head)
        return 127
