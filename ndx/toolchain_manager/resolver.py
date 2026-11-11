"""ToolResolver (single place that finds executables) and ToolEnvironment (per-runtime env/PATH).

Runtimes are never mixed: `host` executables are run by NDX itself; `linux` ones live INSIDE a PRoot rootfs
and are only ever run through a PRoot backend; `termux` ones only through the Termux integration."""
import os
from dataclasses import dataclass

from ..core.paths import is_android

DEFAULT_PRIORITY = ["managed", "project", "external", "system", "proot", "termux"]
ANDROID_SYSTEM_DIRS = ["/system/bin", "/system/xbin", "/vendor/bin", "/apex/com.android.runtime/bin"]
LINUX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PROJECT_BIN_DIRS = [".ndx/bin", ".venv/bin", "venv/bin", "node_modules/.bin"]


@dataclass
class ResolvedTool:
    command: str
    kind: str                 # managed | project | external | system | proot | termux
    path: str                 # host path (managed/project/external/system) or path inside the env / command name
    tool_id: str = ""
    env_id: str = ""          # for kind == proot
    source: str = ""

    @property
    def runtime(self):
        return {"proot": "linux", "termux": "termux"}.get(self.kind, "host")

    @property
    def host_executable(self):
        return self.runtime == "host"

    def describe(self):
        if self.kind == "proot":
            return "%s (inside Linux environment '%s')" % (self.path, self.env_id)
        if self.kind == "termux":
            return "%s (inside Termux)" % self.command
        return self.path


def is_exec(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


class ToolResolver:
    def __init__(self, paths, config, registry, native_lib_dir=None, env_probe=None, termux_probe=None):
        self.paths, self.config, self.registry = paths, config, registry
        self._native = native_lib_dir or _default_native_lib_dir
        self.env_probe = env_probe or (lambda command: [])          # -> [(env_id, path_in_env)]
        self.termux_probe = termux_probe or (lambda command: False)
        self.tools_dir = paths.base / "toolchain"

    # ---- configuration
    def priority(self):
        raw = self.config.get("toolchain.priority") if self.config else None
        if isinstance(raw, list) and raw and all(x in DEFAULT_PRIORITY for x in raw):
            return list(raw)
        return list(DEFAULT_PRIORITY)

    def external_dirs(self):
        raw = self.config.get("toolchain.external_dirs", []) if self.config else []
        return [d for d in raw if isinstance(d, str) and os.path.isabs(d)]

    # ---- locations
    def _managed(self, command):
        m = self.registry.by_command(command)
        if m:
            rec = self.registry.install_record(m.id)
            if rec:
                base = self.tools_dir / m.id / "current"
                exe = base / (rec.get("exe_rel") or m.executable)
                cand = exe if command == os.path.basename(m.executable) else exe.parent / command
                if is_exec(str(cand)):
                    return ResolvedTool(command, "managed", str(cand), m.id, source="NDX toolchain")
        legacy = str(self.paths.bin / command)
        if is_exec(legacy):
            return ResolvedTool(command, "managed", legacy, m.id if m else "", source="NDX bin directory")
        nl = self._native()
        if nl:
            for lib in ({"lib%s.so" % command} | ({x.get("lib") for x in (m.install if m else []) if x.get("lib")}
                                                  if m and command == os.path.basename(m.executable) else set())):
                cand = os.path.join(nl, lib)
                if is_exec(cand):
                    return ResolvedTool(command, "managed", cand, m.id if m else "", source="APK native library")
        return None

    def _project(self, command, project):
        if not project:
            return None
        for rel in PROJECT_BIN_DIRS:
            cand = os.path.join(str(project), rel, command)
            if is_exec(cand):
                return ResolvedTool(command, "project", cand, source="project environment")
        return None

    def _external(self, command):
        for d in self.external_dirs():
            cand = os.path.join(d, command)
            if is_exec(cand):
                return ResolvedTool(command, "external", cand, source="authorized external directory")
        return None

    def _system(self, command):
        dirs = ANDROID_SYSTEM_DIRS if is_android() else [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
        for d in dirs:
            cand = os.path.join(d, command)
            if is_exec(cand):
                return ResolvedTool(command, "system", cand, source="Android system" if is_android() else "host PATH")
        return None

    def _proot(self, command):
        for env_id, p in self.env_probe(command):
            return ResolvedTool(command, "proot", p, env_id=env_id, source="Linux environment")
        return None

    def _termux(self, command):
        if self.termux_probe(command):
            return ResolvedTool(command, "termux", command, source="Termux")
        return None

    # ---- API
    def candidates(self, command, project=None):
        """All matches in priority order."""
        out = []
        fn = {"managed": lambda: self._managed(command), "project": lambda: self._project(command, project),
              "external": lambda: self._external(command), "system": lambda: self._system(command),
              "proot": lambda: self._proot(command), "termux": lambda: self._termux(command)}
        for loc in self.priority():
            r = fn[loc]()
            if r:
                m = self.registry.by_command(command)
                r.tool_id = r.tool_id or (m.id if m else "")
                out.append(r)
        return out

    def resolve(self, tool, runtime=None, project=None):
        """`tool` is a tool id or a command name. runtime: None (any) | host | linux | linux:<env> | termux."""
        m = self.registry.get(tool)
        command = m.commands()[0] if m else tool
        want_env = None
        if runtime and runtime.startswith("linux:"):
            runtime, want_env = "linux", runtime.split(":", 1)[1]
        for r in self.candidates(command, project):
            if runtime and r.runtime != runtime:
                continue
            if want_env and r.env_id != want_env:
                continue
            return r
        return None

    def resolve_path(self, command, project=None):
        """Host-executable path or None (never a path inside PRoot/Termux)."""
        r = self.resolve(command, runtime="host", project=project)
        return r.path if r else None


def _default_native_lib_dir():
    forced = os.environ.get("NDX_NATIVE_LIB_DIR")
    if forced:
        return forced
    if is_android():
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            return str(act.getApplicationInfo().nativeLibraryDir)
        except Exception:
            return None
    return None


class ToolEnvironment:
    """Builds the environment for a runtime. Nothing from one runtime's PATH leaks into another."""

    def __init__(self, paths, registry, resolver):
        self.paths, self.registry, self.resolver = paths, registry, resolver

    def tmp_dir(self):
        d = self.paths.cache / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def tool_vars(self):
        out = {}
        for tid in self.registry.installed_ids():
            m = self.registry.get(tid)
            if not m or not m.env or not self.registry.install_record(tid):
                continue
            rec = self.registry.install_record(tid)
            if rec.get("method") == "native_lib" and rec.get("path"):
                d = os.path.dirname(os.path.abspath(rec["path"]))
                exe_dir = d
            else:
                d = str(self.resolver.tools_dir / tid / "current")
                exe_dir = os.path.dirname(os.path.join(d, rec.get("exe_rel") or m.executable))
            for k, v in m.env.items():
                out[k] = v.replace("{dir}", d).replace("{exe_dir}", exe_dir).replace("{tmp}", self.tmp_dir())
        return out

    def for_runtime(self, runtime="host", project=None, extra=None):
        if runtime == "linux":
            env = {"HOME": "/root", "TERM": "xterm-256color", "LANG": "C.UTF-8", "PATH": LINUX_PATH}
        elif runtime == "termux":
            env = {}
        else:
            env = dict(os.environ)
            dirs = [str(self.paths.bin)]
            for rel in PROJECT_BIN_DIRS:
                if project:
                    p = os.path.join(str(project), rel)
                    if os.path.isdir(p):
                        dirs.append(p)
            dirs += self.resolver.external_dirs()
            dirs += [d for d in env.get("PATH", "").split(os.pathsep) if d]
            seen, ordered = set(), []
            for d in dirs:
                if d not in seen:
                    seen.add(d)
                    ordered.append(d)
            env["PATH"] = os.pathsep.join(ordered)
            env.setdefault("TMPDIR", self.tmp_dir())
            env["NDX_HOME"] = str(self.paths.base)
            env.update(self.tool_vars())
        env.update(extra or {})
        return env
