"""Python execution targets. NDX never assumes that "python3" means the interpreter inside the APK.

  embedded  the interpreter embedded in the APK (in-process on Android; a subprocess of sys.executable on desktop)
  host      an external python3 NDX can execute (managed via the Toolchain Manager, or authorised external)
  venv      the project's virtual environment (<project>/.venv or venv)
  termux    Termux's Python (via RUN_COMMAND; scripts must live in shared storage)
  linux:<id> python3 inside a managed Linux (PRoot) environment (project folder is bound at /project)"""
import importlib.metadata
import os
import re
import sys
import threading
from pathlib import Path

from ..core.errors import NDXError, PermissionDenied, ToolUnavailable, ValidationError
from ..core.paths import is_android
from ..core.registry import Module
from ..compiler_manager.runner import ExecutionTarget
from ..terminal.process import CommandResult
from .embedded import EmbeddedRunner

REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def parse_requirements(path):
    """Distribution names from a requirements.txt (comments, options, URLs and -r includes are skipped)."""
    names = []
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return names
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", ".", "/")):
            continue
        m = REQ_RE.match(line)
        if m:
            names.append(m.group(1))
    return names


def _dist_installed(name):
    try:
        importlib.metadata.distribution(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def venv_python(project):
    for d in (".venv", "venv"):
        for rel in ("bin/python3", "bin/python"):
            p = Path(project) / d / rel
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


class EmbeddedPythonTarget(ExecutionTarget):
    """RunService target: `python3 file.py` run configurations execute in the embedded interpreter."""
    id = "python:embedded"
    title = "Embedded Python (inside the APK)"
    inline = True

    def __init__(self, runtime):
        self.runtime = runtime

    def available(self):
        return True

    def wrap(self, argv, cwd):
        return list(argv), cwd

    def run_inline(self, argv, cwd, emit, cancel):
        if not argv or os.path.basename(argv[0]) not in ("python", "python3") or len(argv) < 2 or argv[1].startswith("-"):
            emit("error: the embedded runtime runs `python3 <script.py> [args]` only\n")
            return 2
        res = self.runtime.run_script(argv[1], argv[2:], target="embedded", cwd=cwd, cancel=cancel, caller="user",
                                      on_output=lambda n, t: emit(t))
        if res.error:
            emit("error: %s\n" % res.error)
        return res.returncode if isinstance(res.returncode, int) else 1


class PythonRuntime(Module):
    name = "python"
    title = "Python Runtime"

    def __init__(self, toolchain, bridge, linux=None, termux=None, consent=None, config=None, audit=None,
                 embedded_mode="auto"):
        self.tc, self.bridge, self.linux, self.termux = toolchain, bridge, linux, termux
        self.consent, self.config = consent, config
        self.audit = audit or (lambda *a, **k: None)
        self.embedded_mode = embedded_mode          # "auto" | "inprocess" | "subprocess"
        self.embedded = EmbeddedRunner()
        self._approved = False
        self._lock = threading.Lock()

    def _inprocess(self):
        return self.embedded_mode == "inprocess" or (self.embedded_mode == "auto" and (is_android() or not sys.executable))

    # ---- targets
    def targets(self, project=None):
        rows = [{"id": "embedded", "kind": "embedded", "title": "Embedded Python (APK)", "available": True,
                 "reason": ("runs inside the NDX process; no pip, limited to bundled packages" if self._inprocess()
                            else "separate interpreter: %s" % sys.executable),
                 "version": "%d.%d.%d" % sys.version_info[:3]}]
        host = self.tc.resolve("python3", runtime="host") if self.tc else None
        rows.append({"id": "host", "kind": "external", "title": "External Python (host)", "available": bool(host),
                     "reason": host.path if host else "not installed: `ndx tool install python3`",
                     "version": self.tc.get_version("python3") if host else None})
        vp = venv_python(project) if project else None
        rows.append({"id": "venv", "kind": "venv", "title": "Project virtual environment", "available": bool(vp),
                     "reason": vp or ("no .venv in the project (create one: ndx python venv <project>)" if project else "needs a project"),
                     "version": None})
        tx_ok = False
        why = "Termux integration is not available"
        if self.termux is not None:
            st = self.termux.get_status()
            tx_ok = st["state"] in ("ready",) or (st["state"] == "no_response" and st["detail"].startswith("not tested"))
            why = "scripts must be in shared storage (/storage/...)" if tx_ok else "%s: %s" % (st["state"], st["guidance"][0])
        rows.append({"id": "termux", "kind": "termux", "title": "Termux Python", "available": tx_ok, "reason": why, "version": None})
        if self.linux is not None:
            for env_id, p in self.linux.probe_command("python3"):
                rows.append({"id": "linux:" + env_id, "kind": "linux", "title": "Python in " + env_id, "available": self.linux.proot_path() is not None,
                             "reason": "%s (project is bound at /project)" % p, "version": None})
        return rows

    def target_ids(self, project=None):
        return [r["id"] for r in self.targets(project) if r["available"]]

    def missing_for_embedded(self, project):
        req = Path(project) / "requirements.txt"
        return [n for n in parse_requirements(req) if not _dist_installed(n)] if req.is_file() else []

    def select(self, project, preferred=None):
        """-> (target_id, [reasons]). Picks where a project should run and says why."""
        avail = {r["id"]: r for r in self.targets(project)}
        if preferred and preferred != "auto":
            if preferred not in avail or not avail[preferred]["available"]:
                raise ToolUnavailable("Python target '%s' is not available: %s" %
                                      (preferred, avail.get(preferred, {}).get("reason", "unknown target")))
            return preferred, ["chosen explicitly"]
        if avail["venv"]["available"]:
            return "venv", ["the project has its own virtual environment"]
        missing = self.missing_for_embedded(project)
        if missing:
            for t in ["host"] + [k for k in avail if k.startswith("linux:")] + ["termux"]:
                if t in avail and avail[t]["available"]:
                    return t, ["requirements not in the embedded runtime: " + ", ".join(missing[:5])]
            return "embedded", ["WARNING: requirements missing from the embedded runtime (%s) and no external Python "
                                "is available; install one: `ndx tool install python3`" % ", ".join(missing[:5])]
        return "embedded", ["no external Python needed"] if not avail["host"]["available"] else ["embedded runtime is enough"]

    # ---- execution
    def run_script(self, script, args=(), target="auto", cwd=None, env=None, timeout=None, cancel=None,
                   on_output=None, caller="user"):
        script = os.path.realpath(str(script))
        if not os.path.isfile(script):
            raise ValidationError("script not found: %s" % script)
        if isinstance(args, str) or not all(isinstance(a, str) for a in args):
            raise ValidationError("args must be a list of strings")
        project = cwd or os.path.dirname(script)
        if target in (None, "auto"):
            target, _why = self.select(project)
        self.audit("python.run", target=target, script=os.path.basename(script), caller=caller)
        if target == "embedded":
            return self._run_embedded(script, list(args), project, env, timeout, cancel, on_output, caller)
        if target == "host":
            exe = self.tc.resolve_path("python3", project=project) if self.tc else None
            if not exe:
                raise ToolUnavailable("no external Python: `ndx tool install python3`")
            return self.bridge.run([exe, "-u", script, *args], "host", project, env, timeout, None, cancel, on_output)
        if target == "venv":
            vp = venv_python(project)
            if not vp:
                raise ToolUnavailable("no virtual environment in %s" % project)
            venv = os.path.dirname(os.path.dirname(vp))
            return self.bridge.run([vp, "-u", script, *args], "host", project,
                                   {**(env or {}), "VIRTUAL_ENV": venv}, timeout, None, cancel, on_output)
        if target == "termux":
            if not script.startswith(("/storage/", "/sdcard/")):
                raise ValidationError("Termux cannot read NDX private storage. Copy the script to shared storage "
                                      "(/storage/emulated/0/...) or use another target.")
            return self.bridge.run(["python3", "-u", script, *args], "termux", None, None, timeout, None, cancel, on_output)
        if target.startswith("linux:"):
            root = os.path.realpath(project)
            if not (script == root or script.startswith(root + os.sep)):
                raise ValidationError("a script run in a Linux environment must be inside the project folder")
            rel = os.path.relpath(script, root)
            return self.bridge.run(["python3", "-u", "/project/" + rel.replace(os.sep, "/"), *args], target, root, env,
                                   timeout, None, cancel, on_output)
        raise ValidationError("unknown Python target: %s" % target)

    def _run_embedded(self, script, args, cwd, env, timeout, cancel, on_output, caller):
        if not self._inprocess():
            return self.bridge.run([sys.executable, "-u", script, *args], "host", cwd, env, timeout, None, cancel, on_output)
        if caller not in ("user", "core", "ui") and not self._approved:
            if self.consent is None or not self.consent.request(
                    "Run Python inside NDX itself?",
                    "The embedded interpreter runs in the same process as NDX, with access to NDX's own data.",
                    ["Script: " + os.path.basename(script), "Only approve code you trust."]):
                raise PermissionDenied("running code in the embedded interpreter was not approved")
            self._approved = True
        r = self.embedded.run(script, args, cwd, env, on_output, cancel, timeout)
        return CommandResult(argv=["<embedded>", script, *args], returncode=r["returncode"], stdout=r["stdout"],
                             stderr=r["stderr"], timed_out=r["timed_out"], cancelled=r["cancelled"], error=r["error"])

    def create_venv(self, project, target="host"):
        project = str(Path(project).resolve())
        if target == "host":
            exe = self.tc.resolve_path("python3") if self.tc else None
            if not exe:
                raise ToolUnavailable("a virtual environment needs an external Python: `ndx tool install python3`")
            r = self.bridge.run([exe, "-m", "venv", ".venv"], "host", project, None, 300)
        elif target.startswith("linux:") or target == "termux":
            raise NDXError("create the virtual environment from inside %s (the project folder is shared)" % target)
        else:
            raise ValidationError("the embedded interpreter cannot create virtual environments")
        if not r.ok:
            raise NDXError("venv creation failed: %s" % (r.stderr or r.error).strip()[-300:])
        return venv_python(project)

    # ---- console
    def cli(self, rest, out):
        rest = list(rest)
        sub = rest.pop(0) if rest else "targets"
        if sub == "targets":
            proj = rest[0] if rest else None
            for r in self.targets(proj):
                out("%-10s %-12s %s\n" % (r["id"], "available" if r["available"] else "UNAVAILABLE", r["reason"]))
            if proj:
                t, why = self.select(proj)
                out("auto -> %s (%s)\n" % (t, "; ".join(why)))
            return 0
        if sub == "run":
            tgt = "auto"
            if "--target" in rest:
                i = rest.index("--target")
                tgt = rest[i + 1]
                del rest[i:i + 2]
            if not rest:
                out("usage: ndx python run [--target T] <script.py> [args]\n")
                return 2
            r = self.run_script(rest[0], rest[1:], tgt, on_output=None)
            out(r.stdout + r.stderr)
            if r.error:
                out("error: %s\n" % r.error)
            return r.returncode if isinstance(r.returncode, int) else 1
        if sub == "venv" and rest:
            out("created %s\n" % self.create_venv(rest[0]))
            return 0
        out("usage: ndx python targets [project] | run [--target T] script [args] | venv <project>\n")
        return 2
