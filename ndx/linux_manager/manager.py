"""Linux environment manager (PRoot backend). Each distro keeps its own rootfs, settings and terminal sessions
under <home>/environments/<id>/.

Security: every environment id is validated (validate_env_id) and every path is built through _dir(), which
canonicalises it and refuses anything that is not a direct child of the managed environments directory.

Install is transactional: backend check -> storage check -> (consent) -> download -> checksum -> safe extract ->
configure -> startup test -> register (env.json is written last). Any failure removes the half-built directory."""
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..core.registry import Module
from ..package_manager import github
from ..shell_environment import ShellProfile
from ..storage import atomic_write, dir_size, disk_usage
from ..utilities import human_size
from .proot import (ProotBackend, build_exec_argv, extract_rootfs, find_proot, resolve_in_rootfs, rootfs_which)
from ..compiler_manager.runner import ExecutionTarget

LIMITATIONS = [
    "PRoot is NOT a virtual machine: it shares the Android kernel.",
    "No independent kernel; no kernel modules, no systemd as init.",
    "Limited hardware access; no automatic GPU acceleration.",
    "Some syscalls are emulated, so some programs fail or run slowly.",
    "No VM-grade security isolation between the environment and Android.",
    "NDX installs PRoot on demand (Toolchain Manager); if no compatible build exists for your device the Linux "
    "environments are shown as unavailable.",
]

ENV_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RESERVED_IDS = {"host", "android", "termux", "proot", "linux", "system", "native", "default"}
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
BIND_RE = re.compile(r"^/[^:\0\n]*(:/[^:\0\n]*)?$")
REQUIRED_DIRS = ("etc", "tmp", "root")
META_SCHEMA = 2

# Rootfs URLs come from upstream project sites. They were NOT fetched/verified in the build environment (no
# network). `sha256_url` points at the sidecar/SHA256SUMS upstream publishes; when it cannot be read the install
# is refused unless the user supplies --sha256 or explicitly approves an unverified download.
_DISTRO_HOSTS = {"alpine": ["dl-cdn.alpinelinux.org"], "ubuntu": ["cdimage.ubuntu.com"], "arch": ["os.archlinuxarm.org"]}
DISTROS = {
    "alpine": {"name": "Alpine Linux", "pkg": "apk", "shell": "/bin/sh", "download_size": 4 * 2 ** 20,
               "installed_size": 12 * 2 ** 20, "hosts": _DISTRO_HOSTS["alpine"],
               "url": {"aarch64": "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/aarch64/alpine-minirootfs-3.20.3-aarch64.tar.gz",
                       "x86_64": "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-minirootfs-3.20.3-x86_64.tar.gz"},
               "sha256_suffix": ".sha256"},
    "ubuntu": {"name": "Ubuntu", "pkg": "apt", "shell": "/bin/bash", "download_size": 30 * 2 ** 20,
               "installed_size": 100 * 2 ** 20, "hosts": _DISTRO_HOSTS["ubuntu"],
               "url": {"aarch64": "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.1-base-arm64.tar.gz",
                       "x86_64": "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.1-base-amd64.tar.gz"},
               "sha256_sums": "https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/SHA256SUMS"},
    "debian": {"name": "Debian", "pkg": "apt", "shell": "/bin/bash", "download_size": 0, "installed_size": 150 * 2 ** 20,
               "hosts": [], "url": {}},   # supply a rootfs URL/file
    "arch": {"name": "Arch Linux ARM", "pkg": "pacman", "shell": "/bin/bash", "download_size": 500 * 2 ** 20,
             "installed_size": 1500 * 2 ** 20, "hosts": _DISTRO_HOSTS["arch"],
             "url": {"aarch64": "https://os.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz"}},
}
_ARCH = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}
_ELF_TO_DISTRO_ARCH = {"arm64-v8a": "aarch64", "x86_64": "x86_64", "armeabi-v7a": "armv7", "x86": "i686"}

OK, WARN, FAIL = "ok", "warn", "fail"


def validate_env_id(env_id):
    """Environment ids are lowercase [a-z0-9_-], 1-32 chars, must start with a letter/digit. Anything else
    (path separators, '..', absolute paths, spaces, unicode, reserved backend names) is rejected."""
    if not isinstance(env_id, str) or not ENV_ID_RE.match(env_id):
        raise ValidationError("invalid environment id %r: use 1-32 characters from a-z, 0-9, '_' and '-', "
                              "starting with a letter or digit" % (env_id if isinstance(env_id, str) else type(env_id).__name__))
    if env_id in RESERVED_IDS:
        raise ValidationError("'%s' is a reserved name; choose another environment id" % env_id)
    return env_id


def _c(name, status, detail=""):
    return {"name": name, "status": status, "detail": detail}


class LinuxManager(Module):
    name = "linux"
    title = "Linux Environments"

    def __init__(self, paths, bus, audit=None, consent=None, tasks=None, toolchain=None, resources=None, downloader=None):
        self.paths, self.bus, self.consent, self.tasks = paths, bus, consent, tasks
        self.toolchain, self.resources, self.downloader = toolchain, resources, downloader
        self.audit = audit or (lambda *a, **k: None)
        self.backend = ProotBackend(paths.bin, toolchain)
        self.root = paths.environments

    # ---- lifecycle / backend state
    def start(self, core):
        self.root.mkdir(parents=True, exist_ok=True)
        self.refresh_status()

    def refresh_status(self, *_a):
        """Called at start and by the Toolchain Manager right after PRoot is installed/removed."""
        if self.backend.available():
            self.status, self.error = "running", ""
        else:
            self.status = "unavailable"
            self.error = "proot is not installed (environments cannot be started). Install it: `ndx tool install proot`"
        if self.bus:
            self.bus.publish("linux.backend", {"available": self.status == "running"}, "linux")

    def proot_path(self):
        return self.backend.path()

    def arch(self):
        return _ARCH.get(platform.machine().lower(), platform.machine().lower())

    # ---- safe paths
    def _dir(self, env_id):
        """Canonical directory of a (validated) environment; always a direct child of the managed root."""
        validate_env_id(env_id)
        root = self.root.resolve()
        d = root / env_id
        if d.is_symlink():
            raise ValidationError("environment directory is a symlink: refusing")
        real = d.resolve()
        if real.parent != root:
            raise ValidationError("environment path escapes the managed environments directory")
        return real

    def _meta_path(self, env_id):
        return self._dir(env_id) / "env.json"

    def _meta(self, env_id):
        try:
            return json.loads(self._meta_path(env_id).read_text())
        except (OSError, ValueError):
            raise NDXError("environment '%s' does not exist or its configuration is unreadable" % env_id)

    def list(self):
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not ENV_ID_RE.match(d.name) or d.is_symlink():
                continue
            meta = d / "env.json"
            if meta.exists():
                try:
                    info = json.loads(meta.read_text())
                    info["id"] = d.name
                    out.append(info)
                except (OSError, ValueError):
                    pass
        return out

    def exists(self, env_id):
        try:
            return self._meta_path(env_id).is_file()
        except ValidationError:
            return False

    def size(self, env_id):
        return dir_size(self._dir(env_id) / "rootfs")

    def rootfs(self, env_id):
        return self._dir(env_id) / "rootfs"

    def package_manager_of(self, env_id):
        return self._meta(env_id).get("pkg", "apt")

    # ---- toolchain integration points
    def probe_command(self, command):
        """[(env_id, path_inside_env)] for every environment that has `command` (used by the resolver)."""
        out = []
        for e in self.list():
            p = rootfs_which(self.root / e["id"] / "rootfs", command)
            if p:
                out.append((e["id"], p))
        return out

    def _proot_env(self):
        return self.backend.env()

    def run_in_env(self, env_id, argv, timeout=60, on_output=None, cancel=None, project_dir=None):
        """Run argv (no shell) inside the environment -> (returncode, output). Used by the toolchain installer."""
        from ..terminal.process import run_process
        proot = self.proot_path()
        if not proot:
            raise ToolUnavailable("proot is not installed: `ndx tool install proot`")
        meta = self._meta(env_id)
        full, _ = self.exec_argv(env_id, argv, project_dir)
        env = dict(os.environ)
        env.update(self._proot_env())
        res = run_process(full, env=env, timeout=timeout, cancel_event=cancel, merge_stderr=True,
                          on_output=(lambda s, t: on_output(t)) if on_output else None)
        return (res.returncode if res.returncode is not None else 1), (res.output or res.error)

    def exec_argv(self, env_id, argv, project_dir=None):
        meta = self._meta(env_id)
        proot = self.proot_path()
        if not proot:
            raise ToolUnavailable("proot is not installed: `ndx tool install proot`")
        binds = self._safe_binds(meta.get("binds", []))
        full = build_exec_argv(proot, self._dir(env_id) / "rootfs", argv, project_dir=project_dir,
                               shell_env=self._safe_env(meta.get("env", {})))
        # extra user binds go right after the fixed ones
        for b in reversed(binds):
            full[full.index("-b") + 0:full.index("-b") + 0] = ["-b", b]
        return full, meta

    @staticmethod
    def _safe_binds(binds):
        return [b for b in binds if isinstance(b, str) and BIND_RE.match(b)]

    @staticmethod
    def _safe_env(env):
        return {k: v for k, v in (env or {}).items() if VAR_RE.match(str(k)) and isinstance(v, str) and "\0" not in v}

    # ---- install
    def _resolve_download(self, distro, info, url, sha256):
        """-> (url, expected_sha256|None, integrity) ; never silently accepts an unverifiable download."""
        from ..toolchain_manager.downloader import ToolDownloader
        dl = self.downloader or ToolDownloader()
        url = url or info["url"].get(self.arch())
        if not url:
            raise ValidationError("no rootfs URL known for %s on %s; provide a tarball or URL" % (info["name"], self.arch()))
        if sha256:
            return url, sha256.lower(), "user-supplied"
        hosts = info.get("hosts") or []
        try:
            if info.get("sha256_suffix") and url == info["url"].get(self.arch()):
                return url, dl.fetch_sidecar_sha256(url + info["sha256_suffix"], hosts, os.path.basename(url)), "sidecar"
            if info.get("sha256_sums") and url == info["url"].get(self.arch()):
                return url, dl.fetch_sidecar_sha256(info["sha256_sums"], hosts, os.path.basename(url)), "sidecar"
        except (NDXError, OSError) as e:
            return url, None, "unverified (checksum unavailable: %s)" % str(e).splitlines()[0]
        return url, None, "unverified"

    def install(self, distro, env_id=None, archive=None, url=None, progress=None, sha256=None,
                require_backend=True, auto_install_proot=False, confirm=True, cancel=None, allow_unverified=False):
        """Install from a local tarball (`archive`) or a URL (catalog default). Synchronous: call from TaskManager."""
        if distro not in DISTROS:
            raise ValidationError("unknown distribution: %s" % distro)
        env_id = validate_env_id(env_id or distro)
        info = DISTROS[distro]
        edir = self._dir(env_id)                         # validated, canonical
        if edir.exists():
            raise ValidationError("environment '%s' already exists" % env_id)
        if cancel is not None and cancel.is_set():
            raise NDXError("cancelled")

        # 1-3: execution backend (before downloading a single byte)
        note = ""
        if require_backend:
            self._ensure_backend(auto_install_proot, confirm, progress)
            ok, detail = self.backend.validate()
            if not ok:
                raise ToolUnavailable("PRoot is installed but not usable on this device: %s\n"
                                      "No root filesystem was downloaded." % detail)
        else:
            note = "backend check skipped"
        # 4: storage
        need = int((info["download_size"] + info["installed_size"]) * 1.2)
        if archive:
            try:
                need = int(os.path.getsize(archive) * 4)
            except OSError:
                pass
        if self.resources is not None:
            ok, i = self.resources.check_storage(self.paths.base, need)
            if not ok:
                raise NDXError("not enough storage to install %s: %s" % (info["name"], i["message"]))
        integrity = "local file"
        expected = None
        if not archive:
            url, expected, integrity = self._resolve_download(distro, info, url, sha256)
            if expected is None and not allow_unverified:
                if not confirm or self.consent is None or not self.consent.request(
                        "Install %s WITHOUT checksum verification?" % info["name"],
                        "No checksum could be obtained for this root filesystem; a tampered download would not be "
                        "detected. Approve only if you trust the source.", [url, integrity]):
                    raise ValidationError("the root filesystem has no verifiable checksum (%s). Provide sha256=... , "
                                          "use a local tarball you verified, or approve the unverified download." % integrity)
                integrity = "unverified-approved"
            elif confirm:
                if self.consent is None or not self.consent.request(
                        "Download Linux root filesystem?", "Downloads and unpacks a root filesystem.",
                        [info["name"], url, "Integrity: %s" % integrity,
                         "Download about %s, on disk about %s" % (human_size(info["download_size"]),
                                                                 human_size(info["installed_size"]))]):
                    raise NDXError("cancelled")
        elif expected is None and sha256:
            expected = sha256.lower()

        edir.mkdir(parents=True)
        try:
            with tempfile.TemporaryDirectory(dir=self.paths.cache) as tmp:
                digest = None
                if not archive:
                    archive = Path(tmp) / "rootfs.tar"
                    hosts = info.get("hosts") or []
                    from ..toolchain_manager.downloader import ToolDownloader
                    dl = self.downloader or ToolDownloader()
                    track = self.resources.track("download", info["name"]) if self.resources else None
                    if track:
                        with track as op:
                            digest = dl.fetch(url, archive, hosts or [__import__("urllib.parse").parse.urlparse(url).hostname],
                                              max_bytes=2 * 1024 ** 3, expected_sha256=expected, cancel=cancel,
                                              progress=lambda d, t: (op.update(d, t), progress and progress(d)))
                    else:
                        digest = dl.fetch(url, archive, hosts, max_bytes=2 * 1024 ** 3, expected_sha256=expected,
                                          cancel=cancel)
                else:
                    archive = Path(archive)
                    if sha256:
                        from ..toolchain_manager.downloader import sha256_file
                        digest = sha256_file(archive)
                        if digest != sha256.lower():
                            raise ValidationError("checksum mismatch: expected %s, got %s" % (sha256.lower(), digest))
                        integrity = "user-supplied"
                if not tarfile.is_tarfile(archive):
                    raise ValidationError("rootfs archive is not a valid tarball")
                rootfs = edir / "rootfs"
                ctx = self.resources.track("extract", env_id) if self.resources else None
                if ctx:
                    with ctx:
                        extract_rootfs(archive, rootfs, progress=progress)
                else:
                    extract_rootfs(archive, rootfs, progress=progress)
            self._configure_rootfs(rootfs)
            meta = {"schema": META_SCHEMA, "distro": distro, "name": info["name"], "pkg": info["pkg"],
                    "shell": info["shell"], "created": time.time(), "env": {}, "binds": [],
                    "arch": self.arch(), "source": url if digest and url else "local", "sha256": digest,
                    "integrity": integrity, "integrity_files": self._fingerprint(rootfs, info["shell"])}
            # startup test needs env.json to exist only for exec_argv's meta lookup: write to a staging name
            atomic_write(edir / "env.json.pending", json.dumps(meta, indent=2))
            os.replace(edir / "env.json.pending", edir / "env.json")
            if require_backend:
                rc, out = self.run_in_env(env_id, ["/bin/sh", "-c", "echo ndx-ok"], timeout=60, cancel=cancel)
                if rc != 0 or "ndx-ok" not in out:
                    raise NDXError("the environment was extracted but did not start (exit %s): %s\n"
                                   "Wrong CPU architecture for this device, or PRoot cannot run here. The incomplete "
                                   "environment was removed." % (rc, out.strip()[-300:]))
                meta["startup_tested"] = time.time()
            else:
                meta["startup_tested"] = None
            atomic_write(edir / "env.json", json.dumps(meta, indent=2))
        except BaseException:
            shutil.rmtree(edir, ignore_errors=True)
            raise
        self.audit("linux.install", env=env_id, distro=distro, integrity=integrity)
        self.bus.publish("linux.changed", {"id": env_id}, "linux")
        return env_id

    def _ensure_backend(self, auto_install_proot, confirm, progress):
        if self.backend.available():
            return
        if not auto_install_proot:
            raise ToolUnavailable("PRoot is not installed, so a Linux environment could not be started. "
                                  "No root filesystem was downloaded.\n  Install PRoot: `ndx tool install proot` "
                                  "(or use the Install PRoot button in Environments).")
        if self.toolchain is None:
            raise ToolUnavailable("PRoot is not installed and no Toolchain Manager is available")
        self.toolchain.install("proot", confirm=confirm, progress=progress)
        self.refresh_status()
        if not self.backend.available():
            raise ToolUnavailable("PRoot installation finished but the executable was not found")

    @staticmethod
    def _configure_rootfs(rootfs):
        rootfs = Path(rootfs)
        for d in ("root", "tmp", "etc", "proc", "dev", "sys", "run"):
            (rootfs / d).mkdir(parents=True, exist_ok=True)
        os.chmod(rootfs / "tmp", 0o1777)
        os.chmod(rootfs / "root", 0o700)
        resolv = rootfs / "etc" / "resolv.conf"
        if resolv.is_symlink():
            resolv.unlink()
        if not resolv.exists() or resolv.stat().st_size == 0:
            resolv.write_text("nameserver 1.1.1.1\nnameserver 8.8.8.8\n")
        hosts = rootfs / "etc" / "hosts"
        if not hosts.exists() or hosts.stat().st_size == 0:
            hosts.write_text("127.0.0.1 localhost\n::1 localhost\n")

    @staticmethod
    def _fingerprint(rootfs, shell):
        """sha256 of a few critical files: later used to detect a damaged rootfs."""
        out = {}
        for rel in (shell, "/bin/sh", "/etc/os-release", "/etc/alpine-release"):
            p = resolve_in_rootfs(rootfs, rel)
            if p is not None and p.is_file() and rel not in out:
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                out[rel] = h.hexdigest()
        return out

    # ---- profile / terminal integration
    def profile(self, env_id):
        meta = self._meta(env_id)
        argv = self.backend.login_argv(self._dir(env_id) / "rootfs", shell=meta["shell"],
                                       extra_binds=self._safe_binds(meta.get("binds", [])),
                                       extra_env=self._safe_env(meta.get("env", {})))
        return ShellProfile("proot-" + env_id, meta["name"] + " (PRoot)", argv, "proot", self._proot_env(),
                            str(self.paths.base))

    # ---- remove / backup / restore
    def remove(self, env_id, confirmed=False):
        edir = self._dir(env_id)
        if not confirmed:
            raise NDXError("removal requires confirmation")
        if not edir.exists():
            raise NDXError("environment '%s' does not exist" % env_id)
        shutil.rmtree(edir)
        self.audit("linux.remove", env=env_id)
        self.bus.publish("linux.changed", {"id": env_id}, "linux")

    def backup(self, env_id, dest_file):
        edir = self._dir(env_id)
        if not (edir / "env.json").is_file():
            raise NDXError("environment '%s' does not exist" % env_id)
        with tarfile.open(dest_file, "w:gz") as tf:
            tf.add(edir, arcname=env_id)
        return dest_file

    def restore(self, archive, env_id, keep_binds=False):
        """Restore a backup made by backup(). Only members under '<env_id>/' are accepted; an existing environment
        is never overwritten and host bind mounts from the archive are dropped unless keep_binds=True."""
        edir = self._dir(env_id)
        if edir.exists():
            raise ValidationError("environment '%s' already exists" % env_id)
        with tarfile.open(archive) as tf:
            names = tf.getnames()
        if not any(posixpath_norm(n) == env_id + "/env.json" for n in names):
            raise ValidationError("not an NDX environment backup for '%s'" % env_id)
        for n in names:
            pn = posixpath_norm(n)
            if pn != env_id and not pn.startswith(env_id + "/"):
                raise ValidationError("backup contains entries outside '%s/': %s" % (env_id, n))
        with tempfile.TemporaryDirectory(dir=self.paths.cache) as tmp:
            extract_rootfs(archive, tmp)
            staged = Path(tmp) / env_id
            if not (staged / "env.json").is_file():
                raise ValidationError("backup has no env.json")
            try:
                meta = json.loads((staged / "env.json").read_text())
            except ValueError:
                raise ValidationError("backup env.json is corrupt")
            if not keep_binds:
                meta["binds"] = []
            meta["env"] = self._safe_env(meta.get("env", {}))
            meta["binds"] = self._safe_binds(meta.get("binds", []))
            atomic_write(staged / "env.json", json.dumps(meta, indent=2))
            shutil.move(str(staged), str(edir))
        self.audit("linux.restore", env=env_id)
        self.bus.publish("linux.changed", {"id": env_id}, "linux")
        return env_id

    def set_env_var(self, env_id, key, value):
        if not isinstance(key, str) or not VAR_RE.match(key):
            raise ValidationError("invalid environment variable name: %r" % (key,))
        if value is not None and (not isinstance(value, str) or "\0" in value):
            raise ValidationError("invalid environment variable value")
        meta_p = self._meta_path(env_id)
        meta = self._meta(env_id)
        if value is None:
            meta["env"].pop(key, None)
        else:
            meta["env"][key] = value
        atomic_write(meta_p, json.dumps(meta, indent=2))

    def set_binds(self, env_id, binds):
        bad = [b for b in binds if not (isinstance(b, str) and BIND_RE.match(b))]
        if bad:
            raise ValidationError("invalid bind mount(s): %s (use /host/path or /host/path:/guest/path)" % bad)
        meta = self._meta(env_id)
        meta["binds"] = list(binds)
        atomic_write(self._meta_path(env_id), json.dumps(meta, indent=2))

    def package_command(self, env_id, action, pkg=""):
        """Returns the argv to run INSIDE the environment (run it via a terminal session)."""
        meta = self._meta(env_id)
        if action != "update" and not re.match(r"^[A-Za-z0-9][A-Za-z0-9.+_:-]*$", pkg or ""):
            raise ValidationError("invalid package name: %r" % (pkg,))
        table = {"apk": {"update": ["apk", "update"], "install": ["apk", "add", pkg], "remove": ["apk", "del", pkg]},
                 "apt": {"update": ["apt-get", "update"], "install": ["apt-get", "install", "-y", pkg],
                         "remove": ["apt-get", "remove", "-y", pkg]},
                 "pacman": {"update": ["pacman", "-Sy"], "install": ["pacman", "-S", "--noconfirm", pkg],
                            "remove": ["pacman", "-R", "--noconfirm", pkg]}}
        return table[meta["pkg"]][action]

    # ---- diagnostics: "Check Linux Environment"
    def check_environment(self, env_id=None, run_startup=True):
        """Detailed diagnostics. -> {"status","checks":[{name,status,detail}], "environments":[...]}.
        With env_id=None only the host side (PRoot, storage, permissions) plus a summary of every environment."""
        checks = self._check_host()
        envs = []
        targets = [env_id] if env_id else [e["id"] for e in self.list()]
        for eid in targets:
            envs.append(self._check_one(eid, run_startup and bool(env_id) or run_startup))
        allc = checks + [c for e in envs for c in e["checks"]]
        worst = FAIL if any(c["status"] == FAIL for c in allc) else WARN if any(c["status"] == WARN for c in allc) else OK
        return {"status": worst, "checks": checks, "environments": envs}

    def _check_host(self):
        out = []
        p = self.proot_path()
        if not p:
            out.append(_c("PRoot executable", FAIL, "not found. Install it: `ndx tool install proot`"))
            return out
        out.append(_c("PRoot executable", OK, p))
        out.append(_c("PRoot permissions", OK if os.access(p, os.X_OK) else FAIL, oct(os.stat(p).st_mode & 0o777)))
        ok, detail = self.backend.validate()
        out.append(_c("PRoot compatibility (format, CPU, runs)", OK if ok else FAIL, detail))
        loader = self._proot_env().get("PROOT_LOADER")
        if loader:
            out.append(_c("PRoot loader", OK if os.path.isfile(loader) else FAIL, loader))
        try:
            free = disk_usage(self.paths.base)["free"]
            out.append(_c("storage availability", OK if free > 300 * 2 ** 20 else WARN, "%s free" % human_size(free)))
        except OSError as e:
            out.append(_c("storage availability", WARN, "cannot measure: %s" % e))
        return out

    def _check_one(self, env_id, run_startup):
        checks = []
        try:
            edir = self._dir(env_id)
        except ValidationError as e:
            return {"id": env_id, "status": FAIL, "checks": [_c("environment id", FAIL, str(e))]}
        try:
            meta = json.loads((edir / "env.json").read_text())
            checks.append(_c("[%s] configuration" % env_id, OK, "%s, package manager %s" % (meta.get("name"), meta.get("pkg"))))
        except (OSError, ValueError) as e:
            return {"id": env_id, "status": FAIL, "checks": [_c("[%s] configuration" % env_id, FAIL, "env.json unreadable: %s" % e)]}
        rootfs = edir / "rootfs"
        missing = [d for d in REQUIRED_DIRS if not (rootfs / d).is_dir()]
        checks.append(_c("[%s] required directories" % env_id, FAIL if missing else OK,
                         ("missing: " + ", ".join(missing)) if missing else ", ".join(REQUIRED_DIRS)))
        shell = resolve_in_rootfs(rootfs, meta.get("shell", "/bin/sh"))
        checks.append(_c("[%s] login shell %s" % (env_id, meta.get("shell")), OK if shell and shell.is_file() else FAIL,
                         str(shell) if shell else "not found inside the root filesystem"))
        # integrity: critical files unchanged since install
        bad = []
        for rel, digest in (meta.get("integrity_files") or {}).items():
            p = resolve_in_rootfs(rootfs, rel)
            if p is None or not p.is_file():
                bad.append("%s missing" % rel)
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                bad.append("%s changed" % rel)
        if meta.get("integrity_files"):
            checks.append(_c("[%s] rootfs integrity (critical files)" % env_id, WARN if bad else OK,
                             ("; ".join(bad) + " (packages may have updated them legitimately)") if bad else "unchanged since install"))
        else:
            checks.append(_c("[%s] rootfs integrity" % env_id, WARN, "no fingerprint recorded at install"))
        # CPU compatibility of the rootfs itself
        if shell and shell.is_file():
            from ..toolchain_manager.arch import read_elf, supported_abis
            elf = read_elf(shell)
            if elf is None:
                checks.append(_c("[%s] compatibility" % env_id, WARN, "login shell is not an ELF binary (script/busybox link?)"))
            else:
                ok = elf["arch"] in supported_abis()
                checks.append(_c("[%s] compatibility" % env_id, OK if ok else FAIL,
                                 "rootfs is %s; device runs %s" % (elf["arch"], "/".join(supported_abis()))))
        # configuration of environment variables / binds
        badv = [k for k in (meta.get("env") or {}) if not VAR_RE.match(str(k))]
        badb = [b for b in (meta.get("binds") or []) if not (isinstance(b, str) and BIND_RE.match(b))]
        missing_b = [b.split(":")[0] for b in self._safe_binds(meta.get("binds") or []) if not os.path.exists(b.split(":")[0])]
        checks.append(_c("[%s] environment configuration" % env_id, FAIL if badv or badb else WARN if missing_b else OK,
                         ("invalid variables %s binds %s" % (badv, badb)) if badv or badb else
                         ("bind source missing: %s" % missing_b) if missing_b else
                         "%d variables, %d bind mounts" % (len(meta.get("env") or {}), len(meta.get("binds") or []))))
        # startup command (and, optionally, an actual start)
        try:
            argv, _m = self.exec_argv(env_id, ["/bin/sh", "-c", "echo ndx-ok"])
            checks.append(_c("[%s] startup command" % env_id, OK, " ".join(argv[:8]) + " ..."))
            if run_startup and self.backend.available():
                rc, out = self.run_in_env(env_id, ["/bin/sh", "-c", "echo ndx-ok"], timeout=60)
                checks.append(_c("[%s] startup test" % env_id, OK if rc == 0 and "ndx-ok" in out else FAIL,
                                 "started and answered" if rc == 0 and "ndx-ok" in out else "exit %s: %s" % (rc, out.strip()[-200:])))
        except ToolUnavailable as e:
            checks.append(_c("[%s] startup command" % env_id, FAIL, str(e).splitlines()[0]))
        checks.append(_c("[%s] size on disk" % env_id, OK, human_size(dir_size(rootfs))))
        status = FAIL if any(c["status"] == FAIL for c in checks) else WARN if any(c["status"] == WARN for c in checks) else OK
        return {"id": env_id, "status": status, "checks": checks}


def posixpath_norm(name):
    import posixpath
    return posixpath.normpath(name).lstrip("/") if name not in ("", ".", "./") else ""


class ProotTarget(ExecutionTarget):
    """Run build/run commands inside a PRoot environment ({project} -> /project)."""

    def __init__(self, manager, env_id):
        self.manager, self.env_id = manager, env_id
        self.id = "proot:" + env_id
        self.title = "PRoot: " + env_id
        self.project_root = "/project"

    def available(self):
        return self.manager.backend.available()

    def env(self):
        return self.manager._proot_env()

    def wrap(self, argv, cwd):
        full, _meta = self.manager.exec_argv(self.env_id, argv, project_dir=str(cwd))
        return full, None
