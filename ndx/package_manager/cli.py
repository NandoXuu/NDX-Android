"""`ndx` command line (runs inside the NDX console, not a system shell)."""
import shlex

from ..core.errors import NDXError, ValidationError

HELP = """ndx commands:
  ndx install <github-url|path.zip|folder> [--version X]
  ndx update <id>        ndx uninstall <id>     ndx rollback <id>
  ndx list               ndx info <id>          ndx search <text> [--offline]
  ndx doctor
  ndx tool ...     install/update/remove/doctor managed tools (ndx tool help)
  ndx git ...      Git (ndx git help)           ndx env list             execution environments
  ndx run [--env E] <tool> [args]               ndx linux list|check|install|remove
  ndx termux status|test|permission|run         ndx python targets|run   ndx service"""


class NdxCli:
    def __init__(self, core):
        self.core = core

    def run(self, line, out):
        try:
            args = shlex.split(line)
        except ValueError as e:
            out("error: %s\n" % e)
            return 2
        if args and args[0] == "ndx":
            args = args[1:]
        if not args or args[0] in ("help", "-h", "--help"):
            out(HELP + "\n")
            return 0
        cmd, rest = args[0], args[1:]
        pm = self.core.services.get("packages")
        from .cli_ext import HANDLERS
        try:
            if cmd in HANDLERS:
                return HANDLERS[cmd](self.core, rest, out) or 0
            fn = getattr(self, "cmd_" + cmd, None)
            if not fn:
                out("unknown command '%s'\n%s\n" % (cmd, HELP))
                return 2
            return fn(pm, rest, out) or 0
        except ValidationError as e:
            out("error: %s\n" % e)
            for p in e.problems:
                out("  - %s\n" % p)
            return 1
        except NDXError as e:
            out("error: %s\n" % e)
            return 1

    @staticmethod
    def _opt(rest, flag):
        if flag in rest:
            i = rest.index(flag)
            val = rest[i + 1] if i + 1 < len(rest) else None
            del rest[i:i + 2]
            return val
        return None

    def cmd_install(self, pm, rest, out):
        rest = list(rest)
        version = self._opt(rest, "--version")
        if not rest:
            out("usage: ndx install <github-url|path> [--version X]\n")
            return 2
        target = rest[0]
        out("installing %s ...\n" % target)
        if target.startswith("https://github.com/"):
            m = pm.install_github(target, version)
        else:
            m = pm.install_path(target, source="local")
        out("installed %s %s\n" % (m.id, m.version))
        if m.has_code:
            out("note: this plugin contains code; enable it explicitly in Packages > Plugins.\n")

    def cmd_update(self, pm, rest, out):
        if not rest:
            out("usage: ndx update <id>\n")
            return 2
        m = pm.update(rest[0])
        out("updated to %s %s\n" % (m.id, m.version))

    def cmd_uninstall(self, pm, rest, out):
        if not rest:
            out("usage: ndx uninstall <id>\n")
            return 2
        pm.uninstall(rest[0])
        out("removed %s\n" % rest[0])

    def cmd_rollback(self, pm, rest, out):
        if not rest:
            out("usage: ndx rollback <id>\n")
            return 2
        out("rolled back to %s\n" % pm.rollback(rest[0]))

    def cmd_list(self, pm, rest, out):
        items = pm.list_installed()
        if not items:
            out("no plugins installed\n")
        for pid, e in sorted(items.items()):
            out("%-24s %-10s %-14s %s%s\n" % (pid, e["version"], e.get("category", ""),
                                                "on" if e.get("enabled") else "off",
                                                " (code approved)" if e.get("code_approved") else ""))

    def cmd_info(self, pm, rest, out):
        if not rest:
            out("usage: ndx info <id>\n")
            return 2
        e = pm.store.get(rest[0])
        if not e:
            out("not installed: %s\n" % rest[0])
            return 1
        m, problems = pm.inspect_dir(pm.store.version_dir(rest[0], e["version"]))
        out("%s %s\n  %s\n  author: %s\n  category: %s\n  permissions: %s\n  code: %s\n  source: %s\n  trust: %s\n" % (
            m.name, m.version, m.description, m.author, m.category, ", ".join(m.permissions) or "none",
            "yes" if m.has_code else "no", e.get("source"),
            self.core.services.get("permissions").trust(m.id)))

    def cmd_search(self, pm, rest, out):
        rest = list(rest)
        offline = "--offline" in rest
        if offline:
            rest.remove("--offline")
        res = pm.search(" ".join(rest), remote=not offline)
        for r in res["installed"]:
            out("[installed] %s %s\n" % (r["id"], r["version"]))
        for r in res["github"]:
            out("[github] %s  *%d  %s\n    %s\n" % (r["full_name"], r["stars"], r["url"], r["description"]))
        if res["error"]:
            out("remote search unavailable: %s\n" % res["error"])

    def cmd_doctor(self, pm, rest, out):
        from ..core.platform_info import summary
        out("platform: %s\n" % summary())
        sh = self.core.services.get("shell")
        out("shells: %s\n" % (", ".join(sh.shells) or "none"))
        for pid in pm.list_installed():
            out("plugin %-20s integrity: %s\n" % (pid, "OK" if pm.verify(pid) else "MODIFIED/MISSING"))
        for m in self.core.modules.all():
            out("module %-12s %s %s\n" % (m.name, m.status, m.error))
