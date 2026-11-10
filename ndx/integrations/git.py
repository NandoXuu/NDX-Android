"""Git integration: a thin, safe wrapper around the real `git` executable (NOT a custom VCS).

* git is found through the Toolchain Manager (or an injected `runner` for Linux/Termux environments).
* argv lists only, never shell strings; refs / URLs / paths are validated so user input cannot become an option.
* NDX never invents an identity: commit-like operations need a configured name+email (or an existing git config).
* GitHub tokens go to git through a throw-away GIT_ASKPASS helper (token only in the child's environment) and are
  redacted from everything this module returns, logs or audits.
"""
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from ..storage import atomic_write

CONFLICT_CODES = frozenset(["DD", "AU", "UD", "UA", "DU", "AA", "UU"])
ALLOWED_PROTOCOLS = "https:ssh:git:file"          # blocks the ext:: transport (arbitrary command execution)
DEFAULT_AUTH_HOSTS = ("github.com",)
MAX_DIFF_BYTES = 2 * 1024 * 1024
NET_TIMEOUT = 300


class GitIdentityMissing(NDXError):
    """No Git user name / email is configured (NDX never makes one up)."""


# --------------------------------------------------------------------------------------------- helpers
def redact(text, secrets=()):
    """Remove secrets and URL credentials from text."""
    if not text:
        return text
    for s in secrets or ():
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    return re.sub(r"(?i)\b((?:https?|ssh|git)://)[^/@\s:]+(?::[^/@\s]*)?@", r"\1***@", text)


_REF_BAD = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")
_REV_BAD = re.compile(r"[\x00-\x20\x7f:?*\[\\]")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _ref_common(name, what, bad):
    if not isinstance(name, str) or not name:
        raise ValidationError("%s must be a non-empty string" % what)
    if name.startswith("-") or name.startswith("/") or name == "@":
        raise ValidationError("invalid %s: %r" % (what, name))
    if bad.search(name) or ".." in name or "@{" in name or "//" in name:
        raise ValidationError("invalid %s: %r" % (what, name))
    if name.endswith(("/", ".", ".lock")) or name.startswith(".") or "/." in name:
        raise ValidationError("invalid %s: %r" % (what, name))
    return name


def validate_ref(name, what="branch/ref name"):
    return _ref_common(name, what, _REF_BAD)


def validate_rev(name):
    """A revision expression such as HEAD~2 or origin/main^ (still never starts with '-')."""
    return _ref_common(name, "revision", _REV_BAD)


def validate_remote_name(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValidationError("invalid remote name: %r" % (name,))
    return name


_SCP_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9._-]*:[^\s]+$")
_TRANSPORT_RE = re.compile(r"^[A-Za-z0-9_.+-]+::")


def validate_url(url):
    """Accept https/ssh/git/file URLs, scp-like git@host:path or an existing local path."""
    if not isinstance(url, str) or not url or url != url.strip() or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValidationError("invalid repository URL")
    if url.startswith("-") or _TRANSPORT_RE.match(url):
        raise ValidationError("repository URL rejected (option or transport-helper syntax): %s" % redact(url))
    if "://" in url:
        u = urlparse(url)
        if u.scheme.lower() not in ("https", "ssh", "git", "file"):
            raise ValidationError("unsupported URL scheme '%s' (use https, ssh, git or file)" % u.scheme)
        if (u.hostname or "").startswith("-"):
            raise ValidationError("invalid host in URL")
        return url
    if _SCP_RE.match(url):
        return url
    if os.path.isabs(url) or os.path.exists(url):
        return url          # absolute paths cannot be options; the directory may not exist yet
    raise ValidationError("not a recognised repository URL or existing local path: %s" % redact(url))


@dataclass
class GitIdentity:
    name: str = ""
    email: str = ""

    def valid(self):
        return bool(self.name.strip()) and bool(re.match(r"^[^\s<>@]+@[^\s<>@]+$", self.email.strip())) \
            and not any(c in self.name for c in "\n\r\0<>")

    @classmethod
    def from_config(cls, config):
        if config is None:
            return cls()
        return cls(str(config.get("git.user_name", "") or ""), str(config.get("git.user_email", "") or ""))


class _hybrid_available:
    """`GitClient.available()` works on the class (host PATH/toolchain) and on an instance (its own resolution)."""

    def __get__(self, obj, cls):
        if obj is None:
            def _cls_available():
                from ..toolchain_manager import host_which
                return host_which("git") is not None
            return _cls_available
        return lambda: obj.git_path() is not None


def parse_conflicts(text):
    """Parse conflict markers into hunks: [{start,end,ours,theirs,base,ours_label,theirs_label}] (1-based lines)."""
    hunks, cur, part = [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("<<<<<<< ") or line == "<<<<<<<":
            cur, part = {"start": n, "ours": [], "theirs": [], "base": None, "ours_label": line[8:].strip(),
                         "theirs_label": ""}, "ours"
        elif cur is not None and line.startswith("|||||||"):
            part, cur["base"] = "base", []
        elif cur is not None and line == "=======":
            part = "theirs"
        elif cur is not None and line.startswith(">>>>>>>"):
            cur["end"], cur["theirs_label"] = n, line[7:].strip()
            hunks.append(cur)
            cur = part = None
        elif cur is not None:
            cur[part].append(line)
    return hunks


class GitClient:
    available = _hybrid_available()

    def __init__(self, path, toolchain=None, runner=None, config=None, secrets=None, audit=None,
                 environment="host", identity=None):
        self.path = str(path)
        self.toolchain, self.runner, self.config, self.secrets = toolchain, runner, config, secrets
        self._audit = audit or (lambda *a, **k: None)
        self.environment = environment
        self._identity = identity

    # ------------------------------------------------------------------------------ resolution
    def git_path(self):
        if self.runner is not None and self.environment != "host":
            return "git"                       # resolved by the environment that runs it
        tc = self.toolchain
        if tc is not None:
            return tc.resolve_path("git")
        from ..toolchain_manager import host_which
        return host_which("git")

    def ensure_available(self, install=False, confirm=True):
        if self.git_path():
            return True
        tc = self.toolchain
        if install and tc is not None and hasattr(tc, "install"):
            try:
                tc.install("git", confirm=confirm)
            except ToolUnavailable as e:
                raise ToolUnavailable("Git is not installed and cannot be installed automatically: %s" % e) from e
            if self.git_path():
                return True
            raise ToolUnavailable("Git was installed but could not be found afterwards (run `ndx tool doctor git`).")
        raise ToolUnavailable("git was not found. Install it with `ndx tool install git`.")

    # ------------------------------------------------------------------------------ identity / auth
    def identity(self):
        if self._identity is not None:
            return self._identity
        return GitIdentity.from_config(self.config)

    def set_identity(self, name, email):
        ident = GitIdentity(str(name).strip(), str(email).strip())
        if not ident.valid():
            raise ValidationError("a valid name and e-mail address are required")
        if self.config is not None and hasattr(self.config, "set"):
            self.config.set("git.user_name", ident.name)
            self.config.set("git.user_email", ident.email)
        self._identity = ident
        return ident

    def _identity_args(self):
        """['-c','user.name=..','-c','user.email=..'] from NDX settings, or [] when git already has an identity."""
        ident = self.identity()
        if ident.valid():
            return ["-c", "user.name=" + ident.name.strip(), "-c", "user.email=" + ident.email.strip()]
        have = []
        for key in ("user.name", "user.email"):
            r = self._exec(["config", "--get", key], timeout=15)
            have.append(r["ok"] and bool(r["stdout"].strip()))
        if all(have):
            return []
        raise GitIdentityMissing("No Git identity is configured. Set your name and e-mail first "
                                 "(`ndx git identity set \"Your Name\" you@example.com`). "
                                 "NDX never uses a made-up identity.")

    def _token(self):
        if self.secrets is not None:
            return self.secrets.get("github_token")
        return os.environ.get("NDX_GITHUB_TOKEN") or None

    def _auth_hosts(self):
        extra = self.config.get("git.auth_hosts", []) if self.config is not None else []
        return {h.lower() for h in list(DEFAULT_AUTH_HOSTS) + [x for x in (extra or []) if isinstance(x, str)]}

    def has_token(self):
        return bool(self._token())

    def _remote_url(self, remote):
        try:
            validate_remote_name(remote)
        except ValidationError:
            return validate_url(remote)
        r = self._exec(["remote", "get-url", remote], timeout=15)
        return r["stdout"].strip() if r["ok"] else ""

    # ------------------------------------------------------------------------------ execution
    def _env(self, extra=None):
        env = dict(os.environ)
        tc = self.toolchain
        if tc is not None and hasattr(tc, "environment"):
            try:
                env = dict(tc.environment("host"))
            except Exception:
                pass
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": ALLOWED_PROTOCOLS, "GIT_EDITOR": "true",
                    "GIT_MERGE_AUTOEDIT": "no", "GIT_PAGER": "cat", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"})
        for k in ("GIT_ASKPASS", "SSH_ASKPASS", "NDX_GIT_TOKEN"):
            env.pop(k, None)
        env.update(extra or {})
        return env

    def _make_askpass(self, token):
        d = tempfile.mkdtemp(prefix="ndx-askpass-")
        os.chmod(d, 0o700)
        script = os.path.join(d, "askpass.sh")
        shebang = "#!/system/bin/sh" if os.path.exists("/system/bin/sh") else "#!/bin/sh"
        fd = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "w") as fh:
            fh.write(shebang + '\ncase "$1" in\n  *sername*) printf "%s\\n" "$NDX_GIT_USER" ;;\n'
                     '  *) printf "%s\\n" "$NDX_GIT_TOKEN" ;;\nesac\n')
        os.chmod(script, stat.S_IRWXU)
        return d, script

    def _exec(self, args, *, cwd=None, timeout=30, cancel=None, auth_url=None, identity=False, env_extra=None):
        """Run git with an argv list. Returns {ok, rc, stdout, stderr, error, timed_out, cancelled} (redacted)."""
        exe = self.git_path()
        if not exe:
            raise ToolUnavailable("git was not found. Install it with `ndx tool install git`.")
        pre = ["--no-pager"] + (self._identity_args() if identity else [])
        argv = [exe] + pre + list(args)
        for a in argv:
            if not isinstance(a, str) or "\0" in a:
                raise ValidationError("invalid argument for git")
        extra = dict(env_extra or {})
        secrets, askdir = [], None
        token = self._token() if auth_url else None
        if token and auth_url:
            u = urlparse(auth_url)
            if u.scheme == "https" and (u.hostname or "").lower() in self._auth_hosts():
                secrets.append(token)
                askdir, script = self._make_askpass(token)
                extra.update({"GIT_ASKPASS": script, "NDX_GIT_TOKEN": token, "NDX_GIT_USER": "x-access-token"})
        env = self._env(extra)
        wd = cwd or self.path
        try:
            if self.runner is not None:
                res = self.runner(argv, wd, env, timeout)
            else:
                from ..terminal.process import run_process
                res = run_process(argv, cwd=wd, env=env, timeout=timeout, cancel_event=cancel)
        finally:
            if askdir:
                shutil.rmtree(askdir, ignore_errors=True)
        out = {"ok": False, "rc": getattr(res, "returncode", None),
               "stdout": redact(getattr(res, "stdout", "") or "", secrets),
               "stderr": redact(getattr(res, "stderr", "") or "", secrets),
               "error": redact(getattr(res, "error", "") or "", secrets),
               "timed_out": bool(getattr(res, "timed_out", False)), "cancelled": bool(getattr(res, "cancelled", False))}
        out["ok"] = out["rc"] == 0 and not out["error"] and not out["timed_out"] and not out["cancelled"]
        if not out["ok"] and not out["error"]:
            out["error"] = (out["stderr"].strip() or out["stdout"].strip() or "git exited with %s" % out["rc"]).splitlines()[-1]
        return out

    def _do(self, label, args, audit=True, **kw):
        res = self._exec(args, **kw)
        if audit:
            self._audit("git." + label, path=self.path, ok=res["ok"],
                        argv=[redact(a) for a in args[:6]])
        return res

    # ------------------------------------------------------------------------------ repository
    def is_repo(self):
        r = self._exec(["rev-parse", "--is-inside-work-tree"], timeout=15)
        return r["ok"] and r["stdout"].strip() == "true"

    def init(self, branch=None):
        os.makedirs(self.path, exist_ok=True)
        args = ["init"]
        if branch:
            args += ["--initial-branch", validate_ref(branch)]
        return self._do("init", args)

    def clone(self, url, dest=None, branch=None, depth=None, timeout=NET_TIMEOUT, cancel=None):
        validate_url(url)
        target = str(dest or self.path)
        parent = os.path.dirname(os.path.abspath(target))
        os.makedirs(parent, exist_ok=True)
        args = ["clone", "--no-hardlinks"] if os.path.exists(url) else ["clone"]
        if branch:
            args += ["--branch", validate_ref(branch)]
        if depth:
            args += ["--depth", str(int(depth))]
        args += ["--", url, os.path.abspath(target)]
        return self._do("clone", args, cwd=parent, timeout=timeout, cancel=cancel, auth_url=url)

    def status(self):
        r = self._exec(["status", "--porcelain=v1", "-b"], timeout=30)
        if not r["ok"]:
            return {"ok": False, "error": r["error"]}
        lines = r["stdout"].splitlines()
        head = lines[0] if lines else ""
        branch = head[3:] if head.startswith("## ") else head
        changes = lines[1:]
        m = re.search(r"\[(?:ahead (\d+))?(?:, )?(?:behind (\d+))?\]\s*$", branch)
        return {"ok": True, "branch": branch, "changes": changes,
                "conflicts": [l[3:] for l in changes if l[:2] in CONFLICT_CODES],
                "ahead": int(m.group(1) or 0) if m else 0, "behind": int(m.group(2) or 0) if m else 0}

    def _paths(self, paths):
        root = Path(self.path).resolve()
        out = []
        for p in paths:
            if not isinstance(p, str) or not p or "\0" in p or p.startswith(":"):
                raise ValidationError("invalid path: %r" % (p,))
            full = (root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
            if full != root and root not in full.parents:
                raise ValidationError("path is outside the repository: %s" % p)
            out.append(os.path.relpath(full, root))
        return out

    def add(self, paths):
        paths = [paths] if isinstance(paths, str) else list(paths)
        if not paths:
            raise ValidationError("nothing to add")
        return self._do("add", ["add", "--"] + self._paths(paths))

    def add_all(self):
        return self._do("add", ["add", "-A"])

    def commit(self, message, amend=False, allow_empty=False):
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("a commit message is required")
        args = ["commit", "-m", message]
        if amend:
            args.append("--amend")
        if allow_empty:
            args.append("--allow-empty")
        return self._do("commit", args, identity=True)

    def log(self, n=20):
        return [e["oneline"] for e in self.log_entries(n)]

    def log_entries(self, n=20, ref=None):
        args = ["log", "-n", str(int(n)), "--format=%H%x1f%an%x1f%ae%x1f%at%x1f%s"]
        if ref:
            args += [validate_rev(ref), "--"]
        r = self._exec(args, timeout=30)
        out = []
        for line in (r["stdout"].splitlines() if r["ok"] else []):
            h, an, ae, at, s = (line.split("\x1f") + [""] * 5)[:5]
            out.append({"hash": h, "author": an, "email": ae, "time": int(at or 0), "subject": s,
                        "oneline": "%s %s" % (h[:7], s)})
        return out

    # ------------------------------------------------------------------------------ branches
    def current_branch(self):
        r = self._exec(["rev-parse", "--abbrev-ref", "HEAD"], timeout=15)
        return r["stdout"].strip() if r["ok"] else ""

    def branches(self, remote=False):
        r = self._exec(["branch", "--no-color", "-a" if remote else "--list"], timeout=15)
        out = []
        for line in (r["stdout"].splitlines() if r["ok"] else []):
            cur = line.startswith("*")
            name = line[2:].strip()
            if " -> " in name or name.startswith("(HEAD detached"):
                continue
            out.append({"name": name, "current": cur, "remote": name.startswith("remotes/")})
        return out

    def create_branch(self, name, start=None, checkout=False):
        validate_ref(name)
        if checkout:
            return self.checkout(name, create=True, start=start)
        return self._do("branch", ["branch", name] + ([validate_rev(start)] if start else []))

    def delete_branch(self, name, force=False):
        return self._do("branch", ["branch", "-D" if force else "-d", validate_ref(name)])

    def rename_branch(self, old, new):
        return self._do("branch", ["branch", "-m", validate_ref(old), validate_ref(new)])

    def checkout(self, ref, create=False, start=None):
        validate_ref(ref)
        args = ["checkout"] + (["-b", ref] + ([validate_rev(start)] if start else []) if create else [ref])
        return self._do("checkout", args)

    def restore(self, paths, staged=False):
        paths = [paths] if isinstance(paths, str) else list(paths)
        return self._do("restore", ["restore"] + (["--staged"] if staged else []) + ["--"] + self._paths(paths))

    # ------------------------------------------------------------------------------ merge / sync
    def merge(self, ref, no_ff=False, ff_only=False, message=None):
        args = ["merge", "--no-edit"] + (["--no-ff"] if no_ff else []) + (["--ff-only"] if ff_only else [])
        if message:
            args += ["-m", message]
        res = self._do("merge", args + [validate_rev(ref)], identity=True)
        if not res["ok"]:
            res["conflicts"] = self.conflicts()
        return res

    def merge_abort(self):
        return self._do("merge", ["merge", "--abort"])

    def fetch(self, remote="origin", prune=False, timeout=NET_TIMEOUT, cancel=None):
        url = self._remote_url(remote)
        return self._do("fetch", ["fetch"] + (["--prune"] if prune else []) + [remote], timeout=timeout,
                        cancel=cancel, auth_url=url)

    def pull(self, remote="origin", branch=None, rebase=False, timeout=NET_TIMEOUT, cancel=None):
        url = self._remote_url(remote)
        args = ["-c", "pull.rebase=%s" % ("true" if rebase else "false"), "pull", "--no-edit", remote]
        if branch:
            args.append(validate_ref(branch))
        res = self._do("pull", args, identity=True, timeout=timeout, cancel=cancel, auth_url=url)
        if not res["ok"]:
            res["conflicts"] = self.conflicts()
        return res

    def push(self, remote="origin", branch=None, set_upstream=False, force_with_lease=False,
             timeout=NET_TIMEOUT, cancel=None):
        url = self._remote_url(remote)
        args = ["push"] + (["-u"] if set_upstream else []) + (["--force-with-lease"] if force_with_lease else [])
        args.append(remote)
        if branch:
            args.append(validate_ref(branch))
        return self._do("push", args, timeout=timeout, cancel=cancel, auth_url=url)

    # ------------------------------------------------------------------------------ remotes
    def remotes(self):
        r = self._exec(["remote", "-v"], timeout=15)
        seen, out = set(), []
        for line in (r["stdout"].splitlines() if r["ok"] else []):
            parts = line.split()
            if len(parts) >= 2 and (parts[0], parts[1]) not in seen:
                seen.add((parts[0], parts[1]))
                out.append({"name": parts[0], "url": redact(parts[1]), "kind": parts[2].strip("()") if len(parts) > 2 else ""})
        return out

    def remote_add(self, name, url):
        return self._do("remote", ["remote", "add", validate_remote_name(name), validate_url(url)])

    def remote_remove(self, name):
        return self._do("remote", ["remote", "remove", validate_remote_name(name)])

    def remote_set_url(self, name, url):
        return self._do("remote", ["remote", "set-url", validate_remote_name(name), validate_url(url)])

    # ------------------------------------------------------------------------------ diff
    def diff(self, path=None, staged=False, ref=None, max_bytes=MAX_DIFF_BYTES):
        args = ["diff", "--no-color", "--no-ext-diff"] + (["--cached"] if staged else [])
        if ref:
            args.append(validate_rev(ref))
        if path:
            args += ["--"] + self._paths([path])
        r = self._exec(args, timeout=60)
        if not r["ok"]:
            return {"ok": False, "error": r["error"]}
        text = r["stdout"]
        trunc = len(text.encode("utf-8", "replace")) > max_bytes
        return {"ok": True, "diff": text[:max_bytes] if trunc else text, "truncated": trunc}

    # ------------------------------------------------------------------------------ conflicts
    def conflicts(self):
        r = self._exec(["status", "--porcelain=v1"], timeout=30)
        return [l[3:] for l in r["stdout"].splitlines() if l[:2] in CONFLICT_CODES] if r["ok"] else []

    def conflict_hunks(self, path):
        full = Path(self.path).resolve() / self._paths([path])[0]
        try:
            text = full.read_text("utf-8", errors="replace")
        except OSError as e:
            raise NDXError("cannot read %s: %s" % (path, e))
        return parse_conflicts(text)

    def resolve_conflict(self, path, strategy):
        """strategy: ours | theirs (takes that side of the whole file) | manual (file already edited by the user)."""
        rel = self._paths([path])[0]
        if strategy in ("ours", "theirs"):
            r = self._do("resolve", ["checkout", "--" + strategy, "--", rel])
            if not r["ok"]:
                return r
        elif strategy != "manual":
            raise ValidationError("strategy must be ours, theirs or manual")
        elif self.conflict_hunks(rel):
            return {"ok": False, "error": "%s still contains conflict markers" % rel, "rc": None, "stdout": "", "stderr": ""}
        return self._do("add", ["add", "--", rel])

    # ------------------------------------------------------------------------------ stash
    def stash(self, message=None):
        return self._do("stash", ["stash", "push"] + (["-m", message] if message else []), identity=True)

    def stash_pop(self):
        return self._do("stash", ["stash", "pop"], identity=True)

    def stash_list(self):
        r = self._exec(["stash", "list"], timeout=15)
        return r["stdout"].splitlines() if r["ok"] else []

    # ------------------------------------------------------------------------------ .gitignore editor
    def gitignore_path(self, subdir=""):
        root = Path(self.path).resolve()
        d = (root / subdir).resolve() if subdir else root
        if d != root and root not in d.parents:
            raise ValidationError("directory is outside the repository: %s" % subdir)
        target = d / ".gitignore"
        if target.is_symlink():
            real = target.resolve()
            if root not in real.parents:
                raise ValidationError(".gitignore is a link that leaves the repository")
        return target

    def gitignore_text(self, subdir=""):
        try:
            return self.gitignore_path(subdir).read_text("utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def gitignore_patterns(self, subdir=""):
        return [l.strip() for l in self.gitignore_text(subdir).splitlines() if l.strip() and not l.lstrip().startswith("#")]

    def gitignore_write(self, text, subdir=""):
        if not isinstance(text, str) or "\0" in text:
            raise ValidationError("invalid .gitignore content")
        atomic_write(self.gitignore_path(subdir), text if text.endswith("\n") or not text else text + "\n")
        return True

    def gitignore_add(self, pattern, subdir=""):
        pattern = self._pattern(pattern)
        text = self.gitignore_text(subdir)
        if pattern in [l.strip() for l in text.splitlines()]:
            return False
        self.gitignore_write(text + ("" if not text or text.endswith("\n") else "\n") + pattern + "\n", subdir)
        return True

    def gitignore_remove(self, pattern, subdir=""):
        pattern = self._pattern(pattern)
        lines = self.gitignore_text(subdir).splitlines()
        keep = [l for l in lines if l.strip() != pattern]
        if len(keep) == len(lines):
            return False
        self.gitignore_write("\n".join(keep) + ("\n" if keep else ""), subdir)
        return True

    @staticmethod
    def _pattern(p):
        if not isinstance(p, str) or not p.strip() or any(c in p for c in "\n\r\0"):
            raise ValidationError("invalid ignore pattern")
        return p.strip()
