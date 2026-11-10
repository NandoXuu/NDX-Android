"""`ndx git ...` commands (run inside the NDX console). Same style as ToolCli."""
import os

from ..core.errors import NDXError, ToolUnavailable, ValidationError
from .git import GitClient

HELP = """ndx git commands (repository: -C <path> | default: the last opened project):
  ndx git status|init|log [N]|diff [--staged] [path]|conflicts
  ndx git clone <url> [dest]            ndx git add <paths...>|-A          ndx git commit -m "msg"
  ndx git branch [list|new <n>|delete <n> [--force]|rename <old> <new>]    ndx git checkout [-b] <ref>
  ndx git merge <ref> [--abort]         ndx git fetch|pull|push [remote] [branch]
  ndx git remote [list|add <n> <url>|remove <n>|set-url <n> <url>]
  ndx git resolve <file> ours|theirs|manual        ndx git stash [pop|list]
  ndx git ignore [list|add <pattern>|remove <pattern>]
  ndx git identity [show|set "<name>" <email>]
  ndx git auth [status|set <token>|clear]          (token: prefer `--from-env VAR` over typing it)
  ndx git install                      (offer to install git through the Toolchain Manager)"""


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


class GitCli:
    def __init__(self, core):
        self.core = core

    def client(self, path):
        sv = self.core.services
        audit = sv.get("audit") or getattr(getattr(self.core, "logs", None), "audit", None)
        pm = sv.get("project_manager")
        kw = dict(getattr(pm, "git_services", None) or {})
        kw.setdefault("toolchain", sv.get("toolchain"))
        kw.setdefault("config", sv.get("config"))
        kw.setdefault("secrets", sv.get("secrets"))
        kw.setdefault("audit", audit)
        return GitClient(path, **kw)

    def _default_path(self):
        cfg = self.core.services.get("config")
        return (cfg.get("session.last_project", "") if cfg else "") or ""

    def run(self, rest, out):
        rest = list(rest)
        path = _opt(rest, "-C") or self._default_path()
        if not rest or rest[0] in ("help", "-h", "--help"):
            out(HELP + "\n")
            return 0
        fn = getattr(self, "cmd_" + rest[0].replace("-", "_"), None)
        if not fn:
            out("unknown git command '%s'\n%s\n" % (rest[0], HELP))
            return 2
        if rest[0] not in ("identity", "auth", "install", "clone") and not path:
            out("no repository: open a project or pass -C <path>\n")
            return 2
        try:
            return fn(self.client(path or "."), rest[1:], out) or 0
        except (ToolUnavailable, ValidationError, NDXError) as e:
            out("error: %s\n" % e)
            return 1

    @staticmethod
    def _show(res, out, ok_text="done"):
        if res.get("ok"):
            text = (res.get("stdout", "") + res.get("stderr", "")).strip()
            out((text or ok_text) + "\n")
            return 0
        out("error: %s\n" % res.get("error", "failed"))
        if res.get("conflicts"):
            out("conflicts: %s\n" % ", ".join(res["conflicts"]))
        return 1

    def cmd_status(self, g, rest, out):
        st = g.status()
        if not st["ok"]:
            out("error: %s\n" % st["error"])
            return 1
        out("branch: %s\n" % st["branch"])
        for c in st["changes"]:
            out("  %s\n" % c)
        if st["conflicts"]:
            out("conflicts: %s\n" % ", ".join(st["conflicts"]))
        return 0

    def cmd_init(self, g, rest, out):
        return self._show(g.init(), out)

    def cmd_clone(self, g, rest, out):
        rest = list(rest)
        if not rest:
            out("usage: ndx git clone <url> [dest]\n")
            return 2
        dest = rest[1] if len(rest) > 1 else None
        if dest is None:
            base = rest[0].rstrip("/").split("/")[-1].split(":")[-1]
            base = base[:-4] if base.endswith(".git") else base
            dest = os.path.join(str(self.core.paths.projects), base or "repo")
        return self._show(g.clone(rest[0], dest), out, "cloned into %s" % dest)

    def cmd_add(self, g, rest, out):
        return self._show(g.add_all() if rest in (["-A"], ["--all"]) else g.add(rest), out)

    def cmd_commit(self, g, rest, out):
        rest = list(rest)
        msg = _opt(rest, "-m")
        if not msg:
            out('usage: ndx git commit -m "message"\n')
            return 2
        return self._show(g.commit(msg, amend=bool(_opt(rest, "--amend", False))), out)

    def cmd_log(self, g, rest, out):
        for line in g.log(int(rest[0]) if rest and rest[0].isdigit() else 20):
            out(line + "\n")
        return 0

    def cmd_diff(self, g, rest, out):
        rest = list(rest)
        staged = _opt(rest, "--staged", False)
        d = g.diff(rest[0] if rest else None, staged=bool(staged))
        out((d["diff"] if d["ok"] else "error: %s\n" % d["error"]) or "(no changes)\n")
        return 0 if d["ok"] else 1

    def cmd_conflicts(self, g, rest, out):
        files = g.conflicts()
        if not files:
            out("no conflicts\n")
        for f in files:
            hunks = g.conflict_hunks(f)
            out("%s: %d conflict hunk(s)\n" % (f, len(hunks)))
            for h in hunks:
                out("  lines %d-%d  ours(%s): %d lines  theirs(%s): %d lines\n" % (
                    h["start"], h["end"], h["ours_label"], len(h["ours"]), h["theirs_label"], len(h["theirs"])))
        return 0

    def cmd_resolve(self, g, rest, out):
        if len(rest) != 2:
            out("usage: ndx git resolve <file> ours|theirs|manual\n")
            return 2
        return self._show(g.resolve_conflict(rest[0], rest[1]), out, "resolved")

    def cmd_branch(self, g, rest, out):
        rest = list(rest)
        sub = rest[0] if rest else "list"
        if sub == "list":
            for b in g.branches():
                out("%s %s\n" % ("*" if b["current"] else " ", b["name"]))
            return 0
        if sub == "new" and len(rest) > 1:
            return self._show(g.create_branch(rest[1]), out)
        if sub == "delete" and len(rest) > 1:
            return self._show(g.delete_branch(rest[1], force="--force" in rest), out)
        if sub == "rename" and len(rest) > 2:
            return self._show(g.rename_branch(rest[1], rest[2]), out)
        out("usage: ndx git branch [list|new <n>|delete <n> [--force]|rename <old> <new>]\n")
        return 2

    def cmd_checkout(self, g, rest, out):
        rest = list(rest)
        create = _opt(rest, "-b", False)
        if not rest:
            out("usage: ndx git checkout [-b] <ref>\n")
            return 2
        return self._show(g.checkout(rest[0], create=bool(create)), out)

    def cmd_merge(self, g, rest, out):
        if rest[:1] == ["--abort"]:
            return self._show(g.merge_abort(), out)
        if not rest:
            out("usage: ndx git merge <ref> | --abort\n")
            return 2
        return self._show(g.merge(rest[0]), out)

    def _sync(self, fn, rest, out):
        remote = rest[0] if rest else "origin"
        branch = rest[1] if len(rest) > 1 else None
        if fn.__name__ == "fetch":
            return self._show(fn(remote), out)
        return self._show(fn(remote, branch), out)

    def cmd_fetch(self, g, rest, out):
        return self._sync(g.fetch, rest, out)

    def cmd_pull(self, g, rest, out):
        return self._sync(g.pull, rest, out)

    def cmd_push(self, g, rest, out):
        return self._sync(g.push, rest, out)

    def cmd_remote(self, g, rest, out):
        sub = rest[0] if rest else "list"
        if sub == "list":
            for r in g.remotes():
                out("%-10s %s %s\n" % (r["name"], r["url"], r["kind"]))
            return 0
        if sub == "add" and len(rest) == 3:
            return self._show(g.remote_add(rest[1], rest[2]), out)
        if sub == "remove" and len(rest) == 2:
            return self._show(g.remote_remove(rest[1]), out)
        if sub == "set-url" and len(rest) == 3:
            return self._show(g.remote_set_url(rest[1], rest[2]), out)
        out("usage: ndx git remote [list|add <n> <url>|remove <n>|set-url <n> <url>]\n")
        return 2

    def cmd_stash(self, g, rest, out):
        if rest[:1] == ["pop"]:
            return self._show(g.stash_pop(), out)
        if rest[:1] == ["list"]:
            out("\n".join(g.stash_list()) + "\n")
            return 0
        return self._show(g.stash(), out)

    def cmd_ignore(self, g, rest, out):
        sub = rest[0] if rest else "list"
        if sub == "list":
            out("\n".join(g.gitignore_patterns()) + "\n")
        elif sub == "add" and len(rest) > 1:
            out("added\n" if g.gitignore_add(rest[1]) else "already present\n")
        elif sub == "remove" and len(rest) > 1:
            out("removed\n" if g.gitignore_remove(rest[1]) else "not found\n")
        else:
            out("usage: ndx git ignore [list|add <pattern>|remove <pattern>]\n")
            return 2
        return 0

    def cmd_identity(self, g, rest, out):
        if rest[:1] == ["set"] and len(rest) == 3:
            ident = g.set_identity(rest[1], rest[2])
            out("Git identity set: %s <%s>\n" % (ident.name, ident.email))
            return 0
        ident = g.identity()
        out("Git identity: %s\n" % (("%s <%s>" % (ident.name, ident.email)) if ident.valid() else
                                    "not set in NDX (git's own config is used if present; NDX never invents one)"))
        return 0

    def cmd_auth(self, g, rest, out):
        rest = list(rest)
        secrets = g.secrets
        if secrets is None:
            out("error: no secret store available\n")
            return 1
        sub = rest[0] if rest else "status"
        if sub == "status":
            src = secrets.source("github_token")
            out("GitHub token: %s\n" % ({"store": "stored (app-private file, not encrypted)",
                                         "env": "from NDX_GITHUB_TOKEN"}.get(src, "not set")))
            return 0
        if sub == "set":
            rest = rest[1:]
            var = _opt(rest, "--from-env")
            token = os.environ.get(var) if var else (rest[0] if rest else None)
            if not token:
                out("usage: ndx git auth set <token> | --from-env VAR\n")
                return 2
            secrets.set("github_token", token)
            out("token saved (it is only sent to github.com over https; console history may still contain what you typed)\n")
            return 0
        if sub == "clear":
            out("token removed\n" if secrets.delete("github_token") else "no stored token\n")
            return 0
        out("usage: ndx git auth [status|set <token>|clear]\n")
        return 2

    def cmd_install(self, g, rest, out):
        g.ensure_available(install=True, confirm=True)
        out("git is available: %s\n" % g.git_path())
        return 0
