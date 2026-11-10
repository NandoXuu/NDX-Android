"""ToolVersionManager, ToolInstaller and ToolUninstaller.

Install transaction (download method):
  consent -> compatibility -> dependencies -> storage check -> download (cache) -> checksum -> safe extract
  into <toolchain>/.staging/ -> locate executable -> validate ELF/arch/loader -> chmod -> test-run ->
  atomic commit into <toolchain>/<id>/<version>/ -> switch the `current` link -> re-verify through the resolver.
Any failure removes the staging area and leaves the previously working version untouched.
Nothing reports success unless the executable was actually run."""
import os
import posixpath
import shutil
import threading
import time
import uuid
from pathlib import Path

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..utilities import human_size
from .arch import ToolValidator, parse_version, run_version
from .downloader import sha256_file
from .extract import extract_archive, guess_kind
from .registry import HOST, TERMUX, env_scope

PERMISSION_HINT = ("Android blocked running the downloaded program. Apps targeting Android 10+ (targetSdk >= 29) "
                   "cannot execute files from their own storage (SELinux). Options: build NDX with a lower "
                   "targetSdk (sideload only), ship the tool inside the APK as lib<name>.so, or use a Linux "
                   "environment / Termux package instead. See docs/TOOLCHAIN.md.")


class ToolVersionManager:
    """Keeps installed versions side by side; `current` is a link that is switched atomically."""

    def __init__(self, tools_dir, bin_dir):
        self.tools_dir, self.bin_dir = Path(tools_dir), Path(bin_dir)

    def tool_dir(self, tid):
        return self.tools_dir / tid

    def version_dir(self, tid, version):
        return self.tool_dir(tid) / version

    def list_versions(self, tid):
        d = self.tool_dir(tid)
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))

    def current(self, tid):
        link = self.tool_dir(tid) / "current"
        try:
            return os.path.basename(os.readlink(link)) if link.is_symlink() else None
        except OSError:
            return None

    def set_current(self, tid, version):
        d = self.tool_dir(tid)
        if not (d / version).is_dir():
            raise ValidationError("%s %s is not installed" % (tid, version))
        tmp = d / (".current-%s" % uuid.uuid4().hex[:8])
        os.symlink(version, tmp)                  # relative link: survives moving NDX_HOME
        os.replace(tmp, d / "current")            # atomic switch

    def prune(self, tid, keep=2, protect=()):
        vs = self.list_versions(tid)
        cur = self.current(tid)
        removable = [v for v in vs if v != cur and v not in protect]
        removable.sort(key=lambda v: (self.version_dir(tid, v)).stat().st_mtime)
        for v in removable[:max(0, len(vs) - keep)]:
            shutil.rmtree(self.version_dir(tid, v), ignore_errors=True)

    def make_shims(self, manifest, exe_rel):
        """bin/<cmd> -> ../toolchain/<id>/current/<dir>/<cmd>. Existing regular files are preserved."""
        made = []
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        exe_dir = posixpath.dirname(exe_rel)
        for cmd in manifest.commands():
            link = self.bin_dir / cmd
            tgt = os.path.relpath(self.tool_dir(manifest.id) / "current" / exe_dir / cmd, self.bin_dir)
            if link.exists() and not link.is_symlink():
                continue                          # a manually placed binary: never overwrite it
            tmp = self.bin_dir / (".%s.%s" % (cmd, uuid.uuid4().hex[:6]))
            try:
                os.symlink(tgt, tmp)
                os.replace(tmp, link)
                made.append(cmd)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return made

    def drop_shims(self, manifest):
        for cmd in manifest.commands():
            link = self.bin_dir / cmd
            if link.is_symlink() and "toolchain" in os.readlink(link):
                link.unlink()


class ToolInstaller:
    def __init__(self, mgr):
        self.m = mgr

    # ------------------------------------------------------------------ public
    def install(self, tool_id, *, version=None, method=None, env=None, force=False, allow_unverified=False,
                sha256=None, from_file=None, progress=None, cancel=None, confirm=True, _stack=()):
        m = self.m
        man = m.registry.require(tool_id)
        if tool_id in _stack:
            raise ValidationError("dependency cycle: %s" % " -> ".join(_stack + (tool_id,)))
        if version and version != man.version:
            if version in m.versions.list_versions(tool_id):
                m.use(tool_id, version)
                return {"id": tool_id, "version": version, "scope": HOST, "method": "switch"}
            raise ValidationError("%s %s is not available (catalog has %s)" % (tool_id, version, man.version))
        lock = m.lock_for(tool_id)
        if not lock.acquire(blocking=False):
            raise NDXError("another operation on '%s' is already running" % tool_id)
        self._p = progress or (lambda *a: None)
        try:
            try:
                res = self._install_locked(man, method, env, force, allow_unverified, sha256, from_file, progress,
                                           cancel, confirm, _stack)
            except BaseException as exc:
                m.audit("tool.install_failed", id=tool_id, error=str(exc))
                m.bus.publish("toolchain.failed", {"id": tool_id, "error": str(exc)}, "toolchain")
                raise
            m.audit("tool.install", id=tool_id, version=res.get("version"), method=res.get("method"),
                    scope=res.get("scope"), integrity=res.get("integrity"))
            m.bus.publish("toolchain.changed", {"id": tool_id, "action": "installed", "scope": res["scope"]}, "toolchain")
            for hook in m.hooks.get(tool_id, []):
                try:
                    hook(res)
                except Exception:
                    pass
            return res
        finally:
            lock.release()

    # ------------------------------------------------------------------ steps
    def _install_locked(self, man, method, env, force, allow_unverified, sha256, from_file, progress, cancel, confirm, stack):
        m = self.m
        chk = m.checker.check(man)       # host CPU/platform: only matters for download/file installs
        host_ok = chk.ok
        method, why = self._choose(man, method, env, from_file, host_ok, chk)
        if method is None:
            raise ToolUnavailable("%s cannot be installed on this device automatically:\n  - %s\n"
                                  "Install it manually: `ndx tool install %s --from-file <path>`." %
                                  (man.name, "\n  - ".join(why) or "no install method available", man.id))
        self._p("plan", 0.0, "method: %s" % method)
        # dependencies first (never silently: they go through the same consent)
        for dep in man.requires:
            if not m.is_installed(dep):
                self._p("dependency", 0.0, "installing dependency %s" % dep)
                self.install(dep, progress=progress, cancel=cancel, confirm=confirm, _stack=stack + (man.id,))
        if method == "download" or method == "file":
            return self._do_download(man, force, allow_unverified, sha256, from_file, cancel, confirm)
        if method == "native_lib":
            return self._do_native(man, confirm)
        if method == "linux_package":
            return self._do_linux_pkg(man, env, cancel, confirm)
        if method == "termux_package":
            return self._do_termux_pkg(man, cancel, confirm)
        raise ToolUnavailable("%s needs a manual install: use --from-file <path>" % man.name)

    def _choose(self, man, method, env, from_file, host_ok, chk):
        m = self.m
        if from_file:
            return ("file", []) if host_ok else (None, chk.problems)
        reasons = []
        order = [x["method"] for x in man.install]
        if method:
            if method not in order:
                return None, ["%s has no '%s' install method" % (man.id, method)]
            order = [method]
        for meth in order:
            if meth == "native_lib":
                lib = man.method("native_lib")["lib"]
                nl = m.resolver._native()
                if nl and os.path.isfile(os.path.join(nl, lib)):
                    return meth, []
                reasons.append("native_lib: %s is not bundled in this build" % lib)
            elif meth == "download":
                if not host_ok:
                    reasons += ["download: " + p for p in chk.problems]
                    continue
                arch, src = m.checker.pick_source(man)
                if not src:
                    reasons.append("download: no build published for %s" % m.checker.arch)
                    continue
                ok, detail = m.exec_probe()
                if not ok:
                    reasons.append("download: " + detail)
                    continue
                return meth, []
            elif meth == "linux_package":
                envs = m.list_envs()
                if env and env not in envs:
                    reasons.append("linux_package: environment '%s' does not exist" % env)
                elif not envs:
                    reasons.append("linux_package: no Linux environment installed (install one in Environments)")
                elif not m.env_runner:
                    reasons.append("linux_package: Linux manager is not available")
                else:
                    return meth, []
            elif meth == "termux_package":
                st = m.termux_status() if m.termux_status else {"state": "unavailable", "detail": "no integration"}
                if st.get("state") == "ready":
                    return meth, []
                reasons.append("termux_package: Termux integration is %s (%s)" % (st.get("state"), st.get("detail", "")))
            elif meth == "manual":
                reasons.append("manual: supply a file with --from-file")
        return None, reasons

    def _consent(self, confirm, title, msg, details):
        if not confirm:
            return
        c = self.m.consent
        if c is None or not c.request(title, msg, details):
            raise NDXError("cancelled: installation was not approved")

    def _unverified_approved(self, man, src, arch, confirm):
        """A download with no published checksum needs its own, separate, informed approval (never implied by
        the ordinary install confirmation, never silent, never available without a live consent channel)."""
        m = self.m
        if not confirm or m.consent is None:
            return False
        if m.config is not None and m.config.get("toolchain.block_unverified_downloads", False):
            return False
        return bool(m.consent.request(
            "Install %s WITHOUT checksum verification?" % man.name,
            "No checksum is published for this download, so NDX cannot prove the file is the one the author "
            "released. NDX still checks the executable format, CPU architecture and that it runs, and it records the "
            "SHA-256 it received, but a tampered download would not be detected. Approve only if you trust the source.",
            ["Tool: %s %s (%s)" % (man.name, man.version, arch), "Source: %s" % src.url,
             "Transport: HTTPS, host allow-list: %s" % ", ".join(man.allowed_hosts),
             "To avoid this prompt, pin a checksum (ndx tool add-manifest) or use --sha256."]))

    def _storage(self, path, required, what):
        res = self.m.resources
        if res is None or not hasattr(res, "check_storage"):
            return
        ok, info = res.check_storage(path, required)
        if not ok:
            raise NDXError("not enough storage to install %s: %s" % (what, info.get("message")))

    # ---------------- download / local file
    def _do_download(self, man, force, allow_unverified, sha256, from_file, cancel, confirm):
        m = self.m
        arch = m.checker.arch
        src = None
        if from_file:
            local = Path(str(from_file).replace("file://", "", 1))
            if not local.is_file():
                raise ValidationError("file not found: %s" % local)
            kind = guess_kind(local)
            expected, integrity = (sha256.lower(), "user-supplied") if sha256 else (None, "unverified")
            if not expected and not allow_unverified:
                raise ValidationError("installing a local file without --sha256 skips integrity verification; "
                                      "pass --sha256 <hex> or --allow-unverified")
            size_dl = local.stat().st_size
            installed = size_dl * 3
            origin = "file:" + str(local)
        else:
            arch, src = m.checker.pick_source(man)
            kind = src.archive
            expected = (sha256 or src.sha256 or "").lower() or None
            integrity = "user-supplied" if sha256 else src.integrity
            if not expected and src.sha256_url:
                self._p("checksum", 0.02, "reading published checksum")
                expected = m.downloader.fetch_sidecar_sha256(src.sha256_url, man.allowed_hosts,
                                                              posixpath.basename(src.url))
                integrity = "sidecar"
            if not expected:
                integrity = "unverified"
                if not allow_unverified and not self._unverified_approved(man, src, arch, confirm):
                    raise ValidationError(
                        "%s: the catalog has no checksum for this download, so NDX will not install it without "
                        "your explicit approval.\n"
                        "  Provide one: ndx tool install %s --sha256 <hex>\n"
                        "  or add a manifest with a pinned checksum, or install from a file you verified, or approve "
                        "the risk in the confirmation dialog / with --allow-unverified." % (man.name, man.id))
                integrity = "unverified-approved"
            size_dl, installed, origin = src.size, src.installed_size or man.size_estimate, src.url
        details = ["Tool: %s %s (%s)" % (man.name, man.version, arch), "Source: %s" % origin,
                   "Integrity: %s%s" % (integrity, "" if expected else " (NOT verified)"),
                   "Download: %s   On disk: %s" % (human_size(size_dl) if size_dl else "unknown size",
                                                   human_size(installed) if installed else "unknown size"),
                   "Destination: %s" % (m.paths.base / "toolchain" / man.id)]
        self._storage(m.paths.base, max((size_dl or 0) + (installed or 0), 1) * 1.2, man.name)
        self._consent(confirm, "Install %s?" % man.name, "NDX will download, verify and install this tool.", details)

        staging = m.staging_dir / ("%s-%s" % (man.id, uuid.uuid4().hex[:8]))
        staging.mkdir(parents=True)
        try:
            artifact = staging / "artifact"
            if from_file:
                self._p("copy", 0.1, "copying file")
                shutil.copyfile(local, artifact)
                digest = sha256_file(artifact)
                if expected and digest != expected:
                    raise ValidationError("checksum mismatch: expected %s, got %s" % (expected, digest))
            else:
                cached = m.cache.get(expected) if expected else None
                if cached:
                    shutil.copyfile(cached, artifact)
                    digest = expected
                    self._p("download", 0.5, "using cached download")
                else:
                    with m.track("download", man.name) as op:
                        def prog(done, total):
                            op.update(done, total)
                            self._p("download", 0.05 + 0.5 * (done / total if total else 0.0),
                                    "%s%s" % (human_size(done), " / " + human_size(total) if total else ""))
                        digest = m.downloader.fetch(src.url, artifact, man.allowed_hosts, expected_sha256=expected,
                                                    progress=prog, cancel=cancel)
                    if expected:
                        m.cache.put(shutil.copyfile(artifact, staging / "artifact.keep"), digest)
            self._p("extract", 0.6, "extracting")
            payload = staging / "payload"
            with m.track("extract", man.name):
                extract_archive(artifact, kind, payload, member_name=posixpath.basename(man.executable), cancel=cancel)
            exe_rel = self._locate(man, payload, src)
            exe = payload / exe_rel
            self._p("validate", 0.75, "validating executable")
            res = m.validator.validate_file(str(exe), man.exe_format, arch)
            if not res.ok:
                raise ValidationError("%s is not usable on this device: %s" % (man.name, res.summary()))
            for d in {str(exe.parent)}:
                for f in Path(d).iterdir():
                    if f.is_file() and not f.is_symlink() and f.stat().st_size > 0 and self._is_elf(f):
                        os.chmod(f, 0o755)
            os.chmod(exe, 0o755)
            self._p("test", 0.82, "running %s %s" % (os.path.basename(exe_rel), " ".join(man.version_args)))
            version = self._test_run(man, exe, payload, exe_rel)
            return self._commit(man, staging, payload, exe_rel, version or man.version, force, {
                "method": "file" if from_file else "download", "source": origin, "sha256": digest,
                "integrity": integrity, "arch": arch})
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _is_elf(p):
        try:
            with open(p, "rb") as fh:
                return fh.read(4) == b"\x7fELF"
        except OSError:
            return False

    @staticmethod
    def _locate(man, payload, src):
        want = [src.member] if src and src.member else []
        want.append(man.executable)
        for w in want:
            if (payload / w).is_file():
                return posixpath.normpath(w)
        base = posixpath.basename(man.executable)
        hits = sorted((p for p in payload.rglob(base) if p.is_file() and not p.is_symlink()),
                      key=lambda p: len(p.relative_to(payload).parts))
        if not hits:
            raise ValidationError("the archive does not contain an executable named '%s'" % base)
        return str(hits[0].relative_to(payload)).replace(os.sep, "/")

    def _test_run(self, man, exe, root, exe_rel):
        env = dict(os.environ)
        exe_dir = str(exe.parent)
        for k, v in man.env.items():
            env[k] = v.replace("{dir}", str(root)).replace("{exe_dir}", exe_dir).replace("{tmp}", self.m.env.tmp_dir())
        ok, out = run_version(str(exe), man.version_args, env=env)
        if not ok:
            hint = ("\n" + PERMISSION_HINT) if "ermission denied" in out else ""
            raise ToolUnavailable("%s was installed but did not run (%s)%s" % (man.name, out[:300] or "no output", hint))
        return parse_version(out, man.version_regex)

    def _commit(self, man, staging, payload, exe_rel, version, force, rec_extra):
        m = self.m
        tdir = m.versions.tool_dir(man.id)
        tdir.mkdir(parents=True, exist_ok=True)
        final = m.versions.version_dir(man.id, version)
        prev_rec = m.registry.install_record(man.id)
        prev_cur = m.versions.current(man.id)
        trash = None
        if final.exists():
            if not force and prev_rec and prev_rec.get("version") == version:
                raise NDXError("%s %s is already installed (use --force to reinstall)" % (man.id, version))
            trash = m.staging_dir / ("trash-%s" % uuid.uuid4().hex[:8])
            os.replace(final, trash)
        try:
            os.replace(payload, final)
            m.versions.set_current(man.id, version)
            rec = {"version": version, "installed": time.time(), "exe_rel": exe_rel,
                   "exe_sha256": sha256_file(final / exe_rel),
                   "versions": m.versions.list_versions(man.id), **rec_extra}
            m.registry.set_install(man.id, HOST, rec)
            made = m.versions.make_shims(man, exe_rel)
            self._p("verify", 0.92, "verifying installation")
            r = m.resolver.resolve(man.id, runtime="host")
            if not r or not r.path.startswith(str(m.versions.tool_dir(man.id))):
                raise NDXError("post-install check failed: %s did not resolve to the managed copy" % man.id)
            version2 = self._test_run(man, Path(r.path), final, exe_rel)
            rec["verified"] = time.time()
            rec["shims"] = made
            m.registry.set_install(man.id, HOST, rec)
        except BaseException:
            # rollback: previous version stays active
            try:
                if prev_cur and (tdir / prev_cur).is_dir():
                    m.versions.set_current(man.id, prev_cur)
                if prev_rec:
                    m.registry.set_install(man.id, HOST, prev_rec)
                else:
                    m.registry.drop_install(man.id, HOST)
                    m.versions.drop_shims(man)
                shutil.rmtree(final, ignore_errors=True)
                if trash and trash.exists():
                    os.replace(trash, final)
            except Exception:
                pass
            raise
        finally:
            if trash:
                shutil.rmtree(trash, ignore_errors=True)
        m.versions.prune(man.id, keep=int(m.config.get("toolchain.keep_versions", 2)) if m.config else 2)
        self._p("done", 1.0, "installed %s %s" % (man.id, version))
        return {"id": man.id, "version": version, "scope": HOST, "path": r.path, **{k: rec_extra[k] for k in rec_extra
                                                                                     if k in ("method", "integrity", "sha256")}}

    # ---------------- bundled native library
    def _do_native(self, man, confirm):
        m = self.m
        lib = man.method("native_lib")["lib"]
        path = os.path.join(m.resolver._native() or "", lib)
        res = m.validator.validate_file(path, man.exe_format, m.checker.arch)
        if not res.ok:
            raise ValidationError("bundled %s is not usable: %s" % (lib, res.summary()))
        env = {k: v.replace("{dir}", os.path.dirname(path)).replace("{exe_dir}", os.path.dirname(path))
               .replace("{tmp}", m.env.tmp_dir()) for k, v in man.env.items()}
        ok, out = run_version(path, man.version_args, env={**os.environ, **env})
        if not ok:
            raise ToolUnavailable("%s did not run: %s" % (lib, out[:300]))
        version = parse_version(out, man.version_regex) or man.version
        m.registry.set_install(man.id, HOST, {"version": version, "installed": time.time(), "method": "native_lib",
                                              "path": path, "verified": time.time(), "integrity": "bundled-in-apk"})
        return {"id": man.id, "version": version, "scope": HOST, "path": path, "method": "native_lib",
                "integrity": "bundled-in-apk"}

    # ---------------- inside a Linux environment
    def _do_linux_pkg(self, man, env, cancel, confirm):
        m = self.m
        envs = m.list_envs()
        env_id = env or envs[0]
        pm = m.env_pkg_manager(env_id)
        spec = man.method("linux_package")["packages"]
        if pm not in spec:
            raise ToolUnavailable("%s has no package for %s (environment '%s')" % (man.name, pm, env_id))
        pkg = spec[pm]
        argvs = {"apt": [["apt-get", "update"], ["apt-get", "install", "-y", "--no-install-recommends", pkg]],
                 "apk": [["apk", "add", "--no-cache", pkg]],
                 "pacman": [["pacman", "-Sy", "--noconfirm", pkg]]}[pm]
        self._storage(m.paths.environments, max(man.size_estimate, 50 * 1024 * 1024) * 1.2, man.name)
        self._consent(confirm, "Install %s in '%s'?" % (man.name, env_id),
                      "Runs the distribution's package manager inside the Linux environment.",
                      ["Environment: " + env_id, "Package: %s (%s)" % (pkg, pm),
                       "Commands: " + " ; ".join(" ".join(a) for a in argvs),
                       "Approx. size: %s" % (human_size(man.size_estimate) if man.size_estimate else "unknown")])
        for i, argv in enumerate(argvs):
            self._p("package", 0.1 + 0.6 * i / len(argvs), " ".join(argv))
            with m.track("package", "%s in %s" % (pkg, env_id)):
                rc, out = m.env_runner(env_id, argv, 3600, lambda t: None, cancel)
            if rc != 0:
                raise NDXError("package install failed in '%s' (exit %s): %s" % (env_id, rc, out[-400:].strip()))
        self._p("verify", 0.9, "verifying")
        rc, out = m.env_runner(env_id, [man.commands()[0]] + list(man.version_args), 60, lambda t: None, cancel)
        if rc != 0:
            raise NDXError("%s was installed but '%s' does not run inside '%s': %s" %
                           (man.name, man.commands()[0], env_id, out[-300:].strip()))
        version = parse_version(out, man.version_regex) or "unknown"
        m.registry.set_install(man.id, env_scope(env_id), {"version": version, "installed": time.time(),
                                                           "method": "linux_package", "package": pkg, "env": env_id,
                                                           "verified": time.time()})
        return {"id": man.id, "version": version, "scope": env_scope(env_id), "method": "linux_package",
                "path": "(inside %s)" % env_id}

    # ---------------- Termux
    def _do_termux_pkg(self, man, cancel, confirm):
        m = self.m
        pkg = man.method("termux_package")["package"]
        self._consent(confirm, "Install %s in Termux?" % man.name, "Asks Termux to run `pkg install` (needs Termux "
                      "allow-external-apps).", ["Package: " + pkg, "Executed by Termux, outside NDX."])
        self._p("package", 0.2, "pkg install %s (via Termux)" % pkg)
        r = m.termux_run("pkg", ["install", "-y", pkg], timeout=1800)
        if r.exit_code != 0:
            raise NDXError("Termux 'pkg install %s' failed (exit %s): %s" % (pkg, r.exit_code, (r.stderr or r.errmsg or "")[-300:]))
        r2 = m.termux_run(man.commands()[0], list(man.version_args), timeout=60)
        if r2.exit_code != 0:
            raise NDXError("%s was installed in Termux but does not run: %s" % (man.name, (r2.stderr or r2.errmsg or "")[-200:]))
        version = parse_version(r2.stdout + r2.stderr, man.version_regex) or "unknown"
        m.registry.set_install(man.id, TERMUX, {"version": version, "installed": time.time(), "method": "termux_package",
                                                "package": pkg, "verified": time.time()})
        return {"id": man.id, "version": version, "scope": TERMUX, "method": "termux_package", "path": "(inside Termux)"}


class ToolUninstaller:
    def __init__(self, mgr):
        self.m = mgr

    def remove(self, tool_id, scope=HOST, confirmed=False, force=False, cancel=None):
        m = self.m
        man = m.registry.require(tool_id)
        if not confirmed:
            raise NDXError("removal requires confirmation")
        rec = m.registry.install_record(tool_id, scope)
        if rec is None:
            raise NDXError("%s is not installed (%s)" % (tool_id, scope))
        deps = m.registry.dependents(tool_id, scope)
        if deps and not force:
            raise NDXError("cannot remove %s: required by %s (use --force)" % (tool_id, ", ".join(deps)))
        lock = m.lock_for(tool_id)
        if not lock.acquire(blocking=False):
            raise NDXError("another operation on '%s' is already running" % tool_id)
        try:
            note = ""
            if scope == HOST:
                if rec.get("method") == "native_lib":
                    note = "bundled in the APK: only the registration was removed"
                else:
                    m.versions.drop_shims(man)
                    shutil.rmtree(m.versions.tool_dir(tool_id), ignore_errors=True)
            elif scope.startswith("env:") and m.env_runner:
                env_id = scope[4:]
                pm = m.env_pkg_manager(env_id)
                pkg = (man.method("linux_package") or {}).get("packages", {}).get(pm)
                if pkg:
                    argv = {"apt": ["apt-get", "remove", "-y", pkg], "apk": ["apk", "del", pkg],
                            "pacman": ["pacman", "-R", "--noconfirm", pkg]}[pm]
                    rc, out = m.env_runner(env_id, argv, 1800, lambda t: None, cancel)
                    if rc != 0:
                        raise NDXError("package removal failed in '%s': %s" % (env_id, out[-300:].strip()))
            elif scope == TERMUX:
                r = m.termux_run("pkg", ["uninstall", "-y", rec.get("package", tool_id)], timeout=600)
                if r.exit_code != 0:
                    raise NDXError("Termux 'pkg uninstall' failed: %s" % (r.stderr or r.errmsg or "")[-200:])
            m.registry.drop_install(tool_id, scope)
        finally:
            lock.release()
        m.audit("tool.remove", id=tool_id, scope=scope)
        m.bus.publish("toolchain.changed", {"id": tool_id, "action": "removed", "scope": scope}, "toolchain")
        return note or "removed"
