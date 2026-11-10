"""ToolchainManager: the single facade other modules use to find, install, verify and remove external tools."""
import contextlib
import os
import subprocess
import threading
import time
from pathlib import Path

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..core.paths import is_android
from ..core.registry import Module
from ..storage import disk_usage
from ..utilities import human_size, which as _which
from .arch import ToolCompatibilityChecker, ToolValidator, parse_version, run_version, supported_abis
from .catalog import PROFILES
from .downloader import ToolCache, ToolDownloader
from .health import ToolHealthChecker
from .installer import PERMISSION_HINT, ToolInstaller, ToolUninstaller, ToolVersionManager
from .registry import HOST, TERMUX, ToolRegistry
from .resolver import ToolEnvironment, ToolResolver

_ACTIVE = None


class _NullOp:
    def update(self, *a, **k):
        pass


@contextlib.contextmanager
def _null_track(*a, **k):
    yield _NullOp()


def active():
    """The most recently created manager (used by modules that have no explicit reference)."""
    return _ACTIVE


def host_which(name, extra_dirs=()):
    """Drop-in replacement for utilities.which that goes through the central resolver (host executables only)."""
    tc = _ACTIVE
    if tc is not None:
        # one resolver for the whole app; extra_dirs are ignored because every authorised location (managed
        # toolchain, legacy NDX bin, project envs, external dirs, system) is already covered by the resolver
        return tc.resolve_path(name)
    return _which(name, extra_dirs)


class ToolchainManager(Module):
    name = "toolchain"
    title = "Toolchain Manager"
    strict = False        # True: never fall back to a raw PATH search

    def __init__(self, paths, bus, config=None, audit=None, consent=None, tasks=None, resources=None,
                 downloader=None, native_lib_dir=None, probe=None):
        global _ACTIVE
        self.paths, self.bus, self.config, self.consent, self.tasks, self.resources = paths, bus, config, consent, tasks, resources
        self.audit = audit or (lambda *a, **k: None)
        self.tools_dir = paths.base / "toolchain"
        self.staging_dir = self.tools_dir / ".staging"
        self.data_dir = paths.data / "toolchain"
        for d in (self.tools_dir, self.staging_dir, self.data_dir / "manifests", paths.bin):
            d.mkdir(parents=True, exist_ok=True)
        self.registry = ToolRegistry(self.data_dir / "manifests", self.data_dir / "state.json")
        self.resolver = ToolResolver(paths, config, self.registry, native_lib_dir=native_lib_dir)
        self.env = ToolEnvironment(paths, self.registry, self.resolver)
        self.checker = ToolCompatibilityChecker()
        self.validator = ToolValidator()
        self.downloader = downloader or ToolDownloader()
        self.cache = ToolCache(paths.cache / "toolchain")
        self.versions = ToolVersionManager(self.tools_dir, paths.bin)
        self.installer = ToolInstaller(self)
        self.uninstaller = ToolUninstaller(self)
        self.health = ToolHealthChecker(self)
        self.hooks = {}
        self._locks, self._locks_guard = {}, threading.Lock()
        self._probe_fn, self._probe_cache = probe, None
        self._version_cache = {}
        # integrations (attached by the Core after the other modules exist)
        self.env_runner = None           # fn(env_id, argv, timeout, on_output, cancel) -> (rc, output)
        self.list_envs = lambda: []
        self.env_pkg_manager = lambda env_id: "apt"
        self.termux = None
        self.termux_status = None
        _ACTIVE = self

    # ---- Module
    def start(self, core):
        self._cleanup_staging()

    def stop(self):
        global _ACTIVE
        if _ACTIVE is self:
            _ACTIVE = None
        self.status = "stopped"

    def _cleanup_staging(self):
        import shutil
        for p in self.staging_dir.iterdir() if self.staging_dir.is_dir() else []:
            if not any(l.locked() for l in self._locks.values()):
                shutil.rmtree(p, ignore_errors=True)

    def diagnostics(self):
        return {"status": self.status, "error": self.error, "installed": self.registry.installed_ids()}

    # ---- integrations
    def attach_linux(self, linux):
        self.list_envs = lambda: [e["id"] for e in linux.list()]
        self.env_pkg_manager = linux.package_manager_of
        self.env_runner = linux.run_in_env
        self.resolver.env_probe = linux.probe_command

    def attach_termux(self, termux):
        self.termux = termux
        self.termux_status = termux.status_for_toolchain
        self.resolver.termux_probe = lambda cmd: termux.has_command(cmd)

    def termux_run(self, command, args, timeout=60):
        if not self.termux:
            raise ToolUnavailable("Termux integration is not available")
        return self.termux.run(command, args, timeout=timeout)

    def lock_for(self, tid):
        with self._locks_guard:
            return self._locks.setdefault(tid, threading.Lock())

    def track(self, kind, name):
        if self.resources is not None and hasattr(self.resources, "track"):
            return self.resources.track(kind, name)
        return _null_track()

    def abis(self):
        return supported_abis()

    def add_hook(self, tool_id, fn):
        self.hooks.setdefault(tool_id, []).append(fn)

    # ---- exec probe
    def exec_probe(self, force=False):
        """Can NDX execute files it writes into its own storage? (Android 10+/targetSdk>=29 says no.)"""
        if self._probe_cache is not None and not force:
            return self._probe_cache
        if self._probe_fn:
            res = self._probe_fn()
        else:
            res = self._probe_real()
        self._probe_cache = res
        return res

    def _probe_real(self):
        shebang = "#!/system/bin/sh" if (is_android() and os.path.exists("/system/bin/sh")) else "#!/bin/sh"
        script = self.paths.bin / (".ndx-exec-probe-%d" % os.getpid())
        try:
            script.write_text(shebang + "\nexit 0\n")
            os.chmod(script, 0o755)
            p = subprocess.run([str(script)], capture_output=True, timeout=10)
            return (p.returncode == 0, "ok" if p.returncode == 0 else "probe script exited %d" % p.returncode)
        except PermissionError as e:
            return False, "executing files from NDX storage is blocked (%s). %s" % (e, PERMISSION_HINT)
        except (OSError, subprocess.SubprocessError) as e:
            return False, "cannot execute files from NDX storage: %s" % e
        finally:
            try:
                script.unlink()
            except OSError:
                pass

    # ---- resolver facade
    def resolve(self, tool, runtime=None, project=None):
        return self.resolver.resolve(tool, runtime, project)

    def resolve_path(self, name, project=None):
        return self.resolver.resolve_path(name, project)

    def which(self, name, extra_dirs=()):
        return self.resolve_path(name)

    def is_installed(self, tool, runtime=None):
        return self.resolver.resolve(tool, runtime=runtime) is not None

    def get_version(self, tool, runtime="host"):
        r = self.resolver.resolve(tool, runtime=runtime)
        if r is None:
            return None
        man = self.registry.by_command(r.command)
        if r.kind == "managed" and man:
            rec = self.registry.install_record(man.id)
            if rec and rec.get("version") and rec.get("method") != "native_lib":
                return rec["version"]
        if r.runtime != "host":
            rec = (self.registry.installs(man.id) if man else {}).get("env:" + r.env_id if r.kind == "proot" else TERMUX)
            return rec.get("version") if rec else None
        args = man.version_args if man else ["--version"]
        try:
            key = (r.path, os.stat(r.path).st_mtime)
        except OSError:
            return None
        if key not in self._version_cache:
            ok, out = run_version(r.path, args, env=self.env.for_runtime("host"))
            self._version_cache[key] = parse_version(out, man.version_regex if man else r"(\d+(?:\.\d+)+)") if ok else None
        return self._version_cache[key]

    def environment(self, runtime="host", project=None, extra=None):
        return self.env.for_runtime(runtime, project, extra)

    # ---- operations (synchronous: call from TaskManager)
    def install(self, tool_id, **kw):
        return self.installer.install(tool_id, **kw)

    def install_async(self, tool_id, **kw):
        if not self.tasks:
            raise NDXError("no task manager available")
        return self.tasks.submit(lambda t: self.install(tool_id, cancel=t.cancel_event, **kw), name="install:" + tool_id,
                                 pass_task=True)

    def remove(self, tool_id, scope=HOST, confirmed=False, force=False):
        return self.uninstaller.remove(tool_id, scope, confirmed=confirmed, force=force)

    def use(self, tool_id, version):
        rec = self.registry.install_record(tool_id)
        if not rec:
            raise NDXError("%s is not installed" % tool_id)
        self.versions.set_current(tool_id, version)
        rec = dict(rec, version=version)
        self.registry.set_install(tool_id, HOST, rec)
        self.bus.publish("toolchain.changed", {"id": tool_id, "action": "switched", "scope": HOST}, "toolchain")

    def update_check(self):
        """[(id, installed_version, catalog_version)] for host installs older than the catalog."""
        from packaging.version import Version
        out = []
        for tid in self.registry.installed_ids():
            man, rec = self.registry.get(tid), self.registry.install_record(tid)
            if man and rec and rec.get("method") in ("download", "file"):
                try:
                    if Version(man.version) > Version(rec["version"]):
                        out.append((tid, rec["version"], man.version))
                except Exception:
                    pass
        return out

    def update(self, tool_id=None, **kw):
        todo = [tool_id] if tool_id else [t for t, _a, _b in self.update_check()]
        results = {}
        for tid in todo:
            results[tid] = self.install(tid, force=False, **kw)
        return results

    def doctor(self, tool_id=None):
        return self.health.run(tool_id)

    # ---- listing / profiles
    def list(self):
        rows = []
        for man in self.registry.all():
            inst = self.registry.installs(man.id)
            r = self.resolver.resolve(man.id)
            rows.append({"id": man.id, "name": man.name, "catalog_version": man.version,
                         "installed": {s: x.get("version") for s, x in inst.items()},
                         "resolved": r.describe() if r else None, "kind": r.kind if r else None,
                         "capabilities": man.capabilities, "required": man.required,
                         "methods": [x["method"] for x in man.install], "origin": man.origin,
                         "source_status": man.source_status, "size_estimate": man.size_estimate})
        return rows

    def profiles(self):
        return {k: dict(v) for k, v in PROFILES.items()}

    def plan_profile(self, name):
        if name not in PROFILES:
            raise ValidationError("unknown profile: %s (%s)" % (name, ", ".join(PROFILES)))
        items, total = [], 0
        for tid in PROFILES[name]["tools"]:
            man = self.registry.require(tid)
            have = self.is_installed(tid)
            _a, src = self.checker.pick_source(man)
            size = 0 if have else (src.installed_size if src and src.installed_size else man.size_estimate)
            total += size
            items.append({"id": tid, "installed": have, "estimated_bytes": size})
        free = None
        try:
            free = disk_usage(self.paths.base)["free"]
        except OSError:
            pass
        return {"profile": name, "title": PROFILES[name]["title"], "tools": items, "estimated_bytes": total,
                "estimate_text": human_size(total) + " (estimate)", "free_bytes": free,
                "fits": None if free is None else total * 1.2 < free}

    def install_profile(self, name, **kw):
        plan = self.plan_profile(name)
        done, failed = [], {}
        for it in plan["tools"]:
            if it["installed"]:
                continue
            try:
                done.append(self.install(it["id"], **kw))
            except NDXError as e:
                failed[it["id"]] = str(e)
        return {"plan": plan, "installed": done, "failed": failed}
