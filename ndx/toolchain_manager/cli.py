"""`ndx tool ...` commands (run inside the NDX console)."""
import json
import time

from ..core.errors import NDXError, ValidationError

HELP = """ndx tool commands:
  ndx tool list [--installed]            ndx tool info <id>            ndx tool resolve <command> [--all]
  ndx tool install <id> [--env ENV] [--method download|native_lib|linux_package|termux_package]
                        [--from-file PATH] [--sha256 HEX] [--allow-unverified] [--force] [--yes]
  ndx tool remove <id> [--scope host|env:NAME|termux] [--force] [--yes]
  ndx tool update [id]      ndx tool use <id> <version>       ndx tool doctor [id]
  ndx tool profile list|show <name>|install <name> [--yes]
  ndx tool add-manifest <file.json>      ndx tool cache clear      ndx tool probe"""


def _opt(rest, flag, takes=True):
    if flag in rest:
        i = rest.index(flag)
        if takes:
            val = rest[i + 1] if i + 1 < len(rest) else None
            del rest[i:i + 2]
            return val
        del rest[i]
        return True
    return None if takes else False


class ToolCli:
    def __init__(self, core):
        self.core = core

    @property
    def tc(self):
        return self.core.services.require("toolchain")

    def run(self, rest, out):
        rest = list(rest)
        if not rest or rest[0] in ("help", "-h", "--help"):
            out(HELP + "\n")
            return 0
        fn = getattr(self, "cmd_" + rest[0].replace("-", "_"), None)
        if not fn:
            out("unknown tool command '%s'\n%s\n" % (rest[0], HELP))
            return 2
        from ..core.errors import NDXError
        try:
            return fn(rest[1:], out) or 0
        except NDXError as exc:
            out("error: %s\n" % exc)
            return 1

    def _progress(self, out):
        last = {"t": 0, "s": None}

        def cb(stage, frac, msg):
            now = time.time()
            if stage != last["s"] or now - last["t"] > 1.0 or frac in (0.0, 1.0):
                last["s"], last["t"] = stage, now
                out("  [%3d%%] %s: %s\n" % (int((frac or 0) * 100), stage, msg))
        return cb

    def cmd_list(self, rest, out):
        only = _opt(rest, "--installed", takes=False)
        for r in self.tc.list():
            if only and not r["installed"]:
                continue
            inst = ", ".join("%s=%s" % kv for kv in r["installed"].items()) or "-"
            out("%-12s %-26s catalog %-8s installed: %-24s %s\n" % (
                r["id"], r["name"], r["catalog_version"], inst,
                ("-> " + r["resolved"]) if r["resolved"] else "(not found)"))
        return 0

    def cmd_info(self, rest, out):
        if not rest:
            out("usage: ndx tool info <id>\n")
            return 2
        m = self.tc.registry.require(rest[0])
        out("%s %s  [%s]\n  %s\n  commands: %s\n  platforms: %s   architectures: %s\n  methods: %s\n  needs: %s\n"
            "  catalog entry: %s\n  notes: %s\n" % (
                m.name, m.version, m.origin, m.description, ", ".join(m.commands()), "/".join(m.platform),
                "/".join(m.architectures), ", ".join(x["method"] for x in m.install) or "-",
                ", ".join(m.requires) or "-", m.source_status, m.notes or "-"))
        out("  installs: %s\n" % json.dumps(self.tc.registry.installs(m.id), default=str))
        return 0

    def cmd_resolve(self, rest, out):
        allv = _opt(rest, "--all", takes=False)
        if not rest:
            out("usage: ndx tool resolve <command> [--all]\n")
            return 2
        cands = self.tc.resolver.candidates(rest[0])
        if not cands:
            out("%s: not found in %s\n" % (rest[0], " > ".join(self.tc.resolver.priority())))
            return 1
        for c in (cands if allv else cands[:1]):
            out("%-9s %s\n" % (c.kind, c.describe()))
        return 0

    def cmd_install(self, rest, out):
        rest = list(rest)
        env, method = _opt(rest, "--env"), _opt(rest, "--method")
        from_file, sha = _opt(rest, "--from-file"), _opt(rest, "--sha256")
        unv, force, yes = (_opt(rest, f, takes=False) for f in ("--allow-unverified", "--force", "--yes"))
        version = _opt(rest, "--version")
        if not rest:
            out("usage: ndx tool install <id> [options]   (see `ndx tool help`)\n")
            return 2
        tid = rest[0]
        out("installing %s ...\n" % tid)
        res = self.tc.install(tid, env=env, method=method, from_file=from_file, sha256=sha, allow_unverified=bool(unv),
                              force=bool(force), confirm=not yes, version=version, progress=self._progress(out))
        out("installed %s %s via %s (%s)\n" % (res["id"], res["version"], res.get("method"), res["scope"]))
        return 0

    def cmd_remove(self, rest, out):
        rest = list(rest)
        scope = _opt(rest, "--scope") or "host"
        force, yes = _opt(rest, "--force", takes=False), _opt(rest, "--yes", takes=False)
        if not rest:
            out("usage: ndx tool remove <id> [--scope S] [--force] [--yes]\n")
            return 2
        if not yes:
            ok = self.core.consent.request("Remove %s?" % rest[0], "The tool (%s) will be uninstalled." % scope, [rest[0]])
            if not ok:
                out("cancelled\n")
                return 1
        out("%s: %s\n" % (rest[0], self.tc.remove(rest[0], scope, confirmed=True, force=bool(force))))
        return 0

    def cmd_update(self, rest, out):
        rest = list(rest)
        if rest:
            pending = [rest[0]]
        else:
            pending = [t for t, _a, _b in self.tc.update_check()]
        if not pending:
            out("everything NDX installed from the catalog is up to date\n")
            return 0
        for tid in pending:
            res = self.tc.install(tid, progress=self._progress(out), confirm=True)
            out("updated %s -> %s\n" % (tid, res["version"]))
        return 0

    def cmd_use(self, rest, out):
        if len(rest) != 2:
            out("usage: ndx tool use <id> <version>\n")
            return 2
        self.tc.use(rest[0], rest[1])
        out("%s now uses %s\n" % tuple(rest))
        return 0

    def cmd_doctor(self, rest, out):
        rep = self.tc.doctor(rest[0] if rest else None)
        icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
        out("toolchain health: %s\n" % rep["status"].upper())
        for c in rep["global"]:
            out("  [%s] %s: %s\n" % (icon[c["status"]], c["name"], c["detail"]))
        for t in rep["tools"]:
            out("  %s [%s] %s %s\n" % (t["id"], t["scope"], t["version"], icon[t["status"]]))
            for c in t["checks"]:
                out("      [%s] %s: %s\n" % (icon[c["status"]], c["name"], c["detail"]))
        return 0 if rep["status"] != "fail" else 1

    def cmd_profile(self, rest, out):
        rest = list(rest)
        yes = _opt(rest, "--yes", takes=False)
        if not rest or rest[0] == "list":
            for k, v in self.tc.profiles().items():
                out("%-9s %-28s %s\n" % (k, v["title"], ", ".join(v["tools"])))
            return 0
        if len(rest) < 2:
            out("usage: ndx tool profile show|install <name>\n")
            return 2
        plan = self.tc.plan_profile(rest[1])
        out("%s: %d tools, about %s to install (free: %s)%s\n" % (
            plan["title"], len(plan["tools"]), plan["estimate_text"],
            "unknown" if plan["free_bytes"] is None else "%d MB" % (plan["free_bytes"] // 2 ** 20),
            "" if plan["fits"] in (True, None) else "  NOT ENOUGH STORAGE"))
        for it in plan["tools"]:
            out("  %-10s %s\n" % (it["id"], "installed" if it["installed"] else "to install"))
        if rest[0] == "install":
            if plan["fits"] is False:
                raise NDXError("not enough free storage for this profile")
            res = self.tc.install_profile(rest[1], confirm=not yes, progress=self._progress(out))
            for r in res["installed"]:
                out("installed %s %s\n" % (r["id"], r["version"]))
            for tid, err in res["failed"].items():
                out("FAILED %s: %s\n" % (tid, err.splitlines()[0]))
            return 1 if res["failed"] else 0
        return 0

    def cmd_add_manifest(self, rest, out):
        if not rest:
            out("usage: ndx tool add-manifest <file.json>\n")
            return 2
        try:
            with open(rest[0], "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise ValidationError("cannot read manifest: %s" % e)
        m = self.tc.registry.add_user_manifest(data)
        out("registered %s %s\n" % (m.id, m.version))
        return 0

    def cmd_cache(self, rest, out):
        if rest[:1] == ["clear"]:
            out("removed %d cached files\n" % self.tc.cache.clear())
        else:
            out("cache: %d bytes\n" % self.tc.cache.size())
        return 0

    def cmd_probe(self, rest, out):
        ok, detail = self.tc.exec_probe(force=True)
        out("%s: %s\n" % ("OK" if ok else "BLOCKED", detail))
        return 0 if ok else 1
