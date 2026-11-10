"""PRoot backend. Requires a `proot` binary: NDX does not bundle one; it is
looked up in <home>/bin and PATH. Without it, environments can be downloaded
and managed but not started (status is reported honestly)."""
import os
import posixpath
import tarfile
from pathlib import Path

from ..core.errors import ValidationError
from ..utilities import which

LINUX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def find_proot(bin_dir, toolchain=None):
    """Executable resolution goes through the Toolchain Manager (managed install, external dirs, system...).
    The bare PATH/bin_dir search is only the fallback when no toolchain exists (stand-alone use)."""
    if toolchain is None:
        from ..toolchain_manager import active
        toolchain = active()
    if toolchain is not None:
        return toolchain.resolve_path("proot")
    return which("proot", [bin_dir])


def build_login_argv(proot, rootfs, shell="/bin/sh", extra_binds=(), extra_env=None):
    env = {"HOME": "/root", "TERM": "xterm-256color", "LANG": "C.UTF-8",
           "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    env.update(extra_env or {})
    argv = [proot, "--link2symlink", "--kill-on-exit", "-0", "-r", str(rootfs), "-w", "/root",
            "-b", "/dev", "-b", "/proc", "-b", "/sys"]
    for b in extra_binds:
        argv += ["-b", b]
    argv += ["/usr/bin/env", "-i"] + ["%s=%s" % kv for kv in env.items()] + [shell, "-l"]
    return argv


def extract_rootfs(archive, dest, progress=None):
    """Extract a rootfs tarball without ever writing outside `dest` (no device
    nodes; symlinks are created but never followed during extraction)."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    with tarfile.open(archive) as tf:
        for m in tf:
            name = posixpath.normpath(m.name).lstrip("/") if m.name not in ("", ".", "./") else ""
            if name in ("", "."):
                continue
            if name == ".." or name.startswith("../"):
                raise ValidationError("rootfs entry escapes destination: %s" % m.name)
            if m.isdev() or m.isfifo():
                continue
            target = (dest / name)
            parent_real = target.parent.resolve()
            if dest != parent_real and dest not in parent_real.parents:
                raise ValidationError("rootfs entry escapes destination: %s" % m.name)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, (m.mode | 0o700) & 0o1777)
            elif m.issym():
                if target.is_symlink() or target.exists():
                    target.unlink()
                os.symlink(m.linkname, target)
            elif m.islnk():
                src = (dest / posixpath.normpath(m.linkname).lstrip("/")).resolve()
                if dest not in src.parents:
                    raise ValidationError("hardlink escapes destination")
                if target.exists():
                    target.unlink()
                if src.exists():
                    os.link(src, target)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    target.unlink()
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                os.chmod(target, ((m.mode & 0o1777) | 0o600))   # owner-writable; setuid/setgid dropped
            count += 1
            if progress and count % 200 == 0:
                progress(count)
    return count


class ProotBackend:
    id = "proot"
    title = "PRoot (userspace chroot)"

    def __init__(self, bin_dir, toolchain=None):
        self.bin_dir = bin_dir
        self.toolchain = toolchain

    def path(self):
        return find_proot(self.bin_dir, self.toolchain)

    def available(self):
        return self.path() is not None

    def env(self):
        """Variables PRoot itself needs (PROOT_LOADER / PROOT_TMP_DIR from the tool manifest)."""
        tc = self.toolchain
        if tc is None:
            from ..toolchain_manager import active
            tc = active()
        if tc is None:
            return {}
        return {k: v for k, v in tc.env.tool_vars().items() if k.startswith("PROOT_")}

    def login_argv(self, rootfs, **kw):
        p = self.path()
        if not p:
            raise ValidationError("proot is not installed. Install it with `ndx tool install proot` "
                                  "(or the Install button in Environments).")
        return build_login_argv(p, rootfs, **kw)

    def validate(self):
        """Is the proot executable usable on this device? -> (ok, detail). Runs it; never assumes."""
        p = self.path()
        if not p:
            return False, "proot executable not found"
        if not os.access(p, os.X_OK):
            return False, "proot is not executable (permissions: %s)" % oct(os.stat(p).st_mode & 0o777)
        from ..toolchain_manager.arch import ToolValidator, run_version
        v = ToolValidator().validate_file(p, "elf") if _is_elf(p) else None
        if v is not None and not v.ok:
            return False, v.summary()
        ok, out = run_version(p, ["--version"], env={**os.environ, **self.env()})
        return ok, (out.splitlines()[0] if out else "no output") if ok else ("proot did not run: %s" % out[:200])


def _is_elf(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def resolve_in_rootfs(rootfs, path, max_links=40):
    """Resolve `path` INSIDE a root filesystem the way a chroot would (absolute symlinks are re-rooted), without
    ever touching the host outside `rootfs`. -> host Path or None."""
    root = Path(rootfs)
    parts = [p for p in str(path).split("/") if p]
    cur, links = [], 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if cur:
                cur.pop()
            continue
        cand = root.joinpath(*cur, part)
        if cand.is_symlink():
            links += 1
            if links > max_links:
                return None
            target = os.readlink(cand)
            if target.startswith("/"):
                cur = []
            parts = [p for p in target.split("/") if p] + parts
            continue
        if not cand.exists():
            return None
        cur.append(part)
    return root.joinpath(*cur)


def rootfs_which(rootfs, command, path_dirs=LINUX_PATH.split(":")):
    """Where would `command` be found inside the rootfs? -> path inside the environment (e.g. /usr/bin/git) or None."""
    for d in path_dirs:
        p = resolve_in_rootfs(rootfs, d.rstrip("/") + "/" + command)
        if p is not None and p.is_file() and os.access(p, os.X_OK):
            return d.rstrip("/") + "/" + command
    return None


def build_exec_argv(proot, rootfs, argv, project_dir=None, shell_env=None):
    """Run `argv` inside the rootfs. The project folder is bound at /project."""
    env = {"HOME": "/root", "TERM": "xterm-256color", "LANG": "C.UTF-8",
           "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    env.update(shell_env or {})
    out = [proot, "--link2symlink", "--kill-on-exit", "-0", "-r", str(rootfs), "-b", "/dev", "-b", "/proc", "-b", "/sys"]
    if project_dir:
        out += ["-b", "%s:/project" % project_dir, "-w", "/project"]
    else:
        out += ["-w", "/root"]
    return out + ["/usr/bin/env", "-i"] + ["%s=%s" % kv for kv in env.items()] + list(argv)
