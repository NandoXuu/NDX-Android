"""Console commands added by the integration work: ndx tool | git | linux | termux | run | env | python | service.
Each handler has the signature fn(core, rest, out) -> exit code."""
import shlex

from ..core.errors import NDXError, ValidationError

ICON = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def _opt(rest, flag, takes=True):
    if flag in rest:
        i = rest.index(flag)
        if takes:
            v = rest[i + 1] if i + 1 < len(rest) else None
            del rest[i:i + 2]
            return v
        del rest[i]
        return True
    return None if takes else False


def cmd_tool(core, rest, out):
    from ..toolchain_manager import ToolCli
    return ToolCli(core).run(rest, out)


def cmd_git(core, rest, out):
    from ..integrations.git_cli import GitCli
    return GitCli(core).run(rest, out)


def cmd_env(core, rest, out):
    """ndx env list : execution environments the bridge can use, with honest availability."""
    for r in core.services.require("bridge").environments():
        out("%-12s %-8s %-12s %s\n" % (r["id"], r["backend"], "available" if r["available"] else "UNAVAILABLE", r["reason"]))
    return 0


def cmd_run(core, rest, out):
    """ndx run [--env ENV] [--cwd DIR] [--timeout S] <tool> [args...]   (argument array, no shell)"""
    rest = list(rest)
    env, cwd, to = _opt(rest, "--env") or "host", _opt(rest, "--cwd"), _opt(rest, "--timeout")
    if not rest:
        out("usage: ndx run [--env ENV] [--cwd DIR] [--timeout S] <tool> [args...]\n")
        return 2
    res = core.services.require("tools").run(rest[0], rest[1:], env, cwd=cwd, timeout=float(to) if to else None,
                                             caller="user")
    out(res.stdout)
    if res.stderr:
        out(res.stderr)
    if res.error:
        out("error: %s\n" % res.error)
    return res.returncode if isinstance(res.returncode, int) else 1


def cmd_termux(core, rest, out):
    tx = core.services.require("termux")
    sub = rest[0] if rest else "status"
    if sub in ("status", "test"):
        st = tx.get_status(probe=(sub == "test"), force=(sub == "test"))
        out("Termux: %s - %s\n" % (st["state"], st["detail"]))
        for g in st["guidance"]:
            out("  - %s\n" % g)
        out("Termux:API app: %s\n" % {True: "installed", False: "not installed", None: "unknown"}[st["termux_api"]])
        return 0 if st["state"] == "ready" else 1
    if sub == "permission":
        out("requested\n" if tx.request_permission() else "could not request the permission\n")
        return 0
    if sub == "run" and len(rest) > 1:
        r = tx.run(rest[1], rest[2:])
        out(r.stdout + r.stderr)
        return r.exit_code if isinstance(r.exit_code, int) else 1
    out("usage: ndx termux status|test|permission|run <command> [args...]\n")
    return 2


def _print_checks(rep, out):
    out("overall: %s\n" % rep["status"].upper())
    for c in rep["checks"]:
        out("  [%s] %s: %s\n" % (ICON[c["status"]], c["name"], c["detail"]))
    for e in rep["environments"]:
        out("environment %s: %s\n" % (e["id"], e["status"].upper()))
        for c in e["checks"]:
            out("  [%s] %s: %s\n" % (ICON[c["status"]], c["name"], c["detail"]))


def cmd_linux(core, rest, out):
    lm = core.services.require("linux")
    rest = list(rest)
    sub = rest.pop(0) if rest else "list"
    if sub == "list":
        for e in lm.list():
            out("%-16s %-14s %s\n" % (e["id"], e["name"], e.get("integrity", "")))
        if not lm.list():
            out("no environments installed\n")
        return 0
    if sub == "check":
        no_start = _opt(rest, "--no-start", takes=False)
        rep = lm.check_environment(rest[0] if rest else None, run_startup=not no_start)
        _print_checks(rep, out)
        return 1 if rep["status"] == "fail" else 0
    if sub == "install":
        eid, archive, sha = _opt(rest, "--id"), _opt(rest, "--archive"), _opt(rest, "--sha256")
        auto, yes = _opt(rest, "--install-proot", takes=False), _opt(rest, "--yes", takes=False)
        if not rest:
            out("usage: ndx linux install <distro> [--id ID] [--archive FILE] [--sha256 HEX] [--install-proot] [--yes]\n")
            return 2
        out("installed %s\n" % lm.install(rest[0], env_id=eid, archive=archive, sha256=sha,
                                          auto_install_proot=bool(auto), confirm=not yes))
        return 0
    if sub == "remove" and rest:
        lm.remove(rest[0], confirmed=bool(_opt(rest, "--yes", takes=False)) or core.consent.request(
            "Remove environment?", "Delete %s and everything inside it?" % rest[0], [rest[0]]))
        out("removed %s\n" % rest[0])
        return 0
    out("usage: ndx linux list|check [id] [--no-start]|install <distro> ...|remove <id>\n")
    return 2


def cmd_python(core, rest, out):
    py = core.services.get("python")
    if py is None:
        out("python runtime service is not available\n")
        return 1
    return py.cli(rest, out)


def cmd_service(core, rest, out):
    sm = core.services.require("servers")
    link = getattr(sm, "background", None)
    if link is None:
        out("background service link is not available\n")
        return 1
    st = link.status()
    out("background execution: %s\n" % st["summary"])
    for line in st.get("details", []):
        out("  - %s\n" % line)
    return 0


HANDLERS = {"tool": cmd_tool, "git": cmd_git, "env": cmd_env, "run": cmd_run, "termux": cmd_termux,
            "linux": cmd_linux, "python": cmd_python, "service": cmd_service}
