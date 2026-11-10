"""ToolHealthChecker: verifies what is *actually* on disk, not what the state file claims."""
import os
from pathlib import Path

from ..storage import disk_usage
from .arch import parse_version, run_version
from .downloader import sha256_file
from .registry import HOST, TERMUX

OK, WARN, FAIL = "ok", "warn", "fail"


def _c(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


class ToolHealthChecker:
    def __init__(self, mgr):
        self.m = mgr

    def global_checks(self):
        m = self.m
        out = []
        ok, detail = m.exec_probe()
        out.append(_c("executables can run from NDX storage", OK if ok else FAIL, detail))
        out.append(_c("architecture / platform", OK,
                      "%s on %s (ABIs: %s)" % (m.checker.arch, m.checker.platform, ", ".join(m.abis()))))
        try:
            du = disk_usage(m.paths.base)
            out.append(_c("free storage", OK if du["free"] > 200 * 1024 * 1024 else WARN, "%d MB free" % (du["free"] // 2 ** 20)))
        except OSError as e:
            out.append(_c("free storage", WARN, "cannot measure: %s" % e))
        stale = [p.name for p in m.staging_dir.iterdir()] if m.staging_dir.is_dir() else []
        out.append(_c("leftover staging directories", OK if not stale else WARN,
                      "none" if not stale else "%d (cleaned on next start)" % len(stale)))
        for f, e in m.registry.errors.items():
            out.append(_c("user manifest %s" % f, FAIL, e))
        out.append(_c("priority order", OK, " > ".join(m.resolver.priority())))
        return out

    def check_tool(self, tid):
        m = self.m
        man = m.registry.get(tid)
        results = []
        for scope, rec in sorted(m.registry.installs(tid).items()):
            checks = []
            if scope == HOST:
                if rec.get("method") == "native_lib":
                    path = rec.get("path", "")
                else:
                    cur = m.versions.current(tid)
                    checks.append(_c("current version link", OK if cur == rec.get("version") else FAIL,
                                     "link=%s state=%s" % (cur, rec.get("version"))))
                    path = str(m.versions.tool_dir(tid) / "current" / (rec.get("exe_rel") or man.executable)) if man else ""
                exists = os.path.isfile(path)
                checks.append(_c("executable present", OK if exists else FAIL, path))
                if exists:
                    checks.append(_c("executable permission", OK if os.access(path, os.X_OK) else FAIL, oct(os.stat(path).st_mode & 0o777)))
                    if man:
                        v = m.validator.validate_file(path, man.exe_format)
                        checks.append(_c("executable format / ABI", OK if v.ok else FAIL, v.summary()))
                    if rec.get("exe_sha256"):
                        same = sha256_file(path) == rec["exe_sha256"]
                        checks.append(_c("file unchanged since install", OK if same else FAIL, "sha256 " + ("matches" if same else "DIFFERS")))
                    if man and os.access(path, os.X_OK):
                        env = {**os.environ, **m.env.tool_vars()}
                        good, out = run_version(path, man.version_args, env=env)
                        checks.append(_c("runs (%s)" % " ".join(man.version_args), OK if good else FAIL,
                                         (parse_version(out, man.version_regex) or out[:120]) if good else out[:200]))
                r = m.resolver.resolve(tid, runtime="host")
                checks.append(_c("resolved by NDX", OK if r else FAIL, r.describe() if r else "not found by the resolver"))
                if man and r and r.kind != "managed":
                    checks.append(_c("shadowed", WARN, "%s takes priority over the NDX copy" % r.kind))
            elif scope.startswith("env:"):
                env_id = scope[4:]
                found = [p for e, p in m.resolver.env_probe(man.commands()[0]) if e == env_id] if man else []
                checks.append(_c("present in environment '%s'" % env_id, OK if found else FAIL, found[0] if found else "not found"))
            elif scope == TERMUX:
                checks.append(_c("Termux install", WARN, "recorded at install time; NDX cannot re-check Termux without running a command"))
            status = FAIL if any(c["status"] == FAIL for c in checks) else WARN if any(c["status"] == WARN for c in checks) else OK
            results.append({"id": tid, "scope": scope, "status": status, "version": rec.get("version"), "checks": checks})
        return results

    def run(self, tool_id=None):
        ids = [tool_id] if tool_id else self.m.registry.installed_ids()
        tools = []
        for tid in ids:
            tools.extend(self.check_tool(tid))
        glob = self.global_checks()
        worst = FAIL if any(t["status"] == FAIL for t in tools) or any(c["status"] == FAIL for c in glob) else \
            WARN if any(t["status"] == WARN for t in tools) or any(c["status"] == WARN for c in glob) else OK
        return {"status": worst, "global": glob, "tools": tools}
