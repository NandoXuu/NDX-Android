import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ndx.core.errors import NDXError, ToolUnavailable, ValidationError  # noqa: E402
from ndx.integrations import (GitClient, GitCli, GitIdentityMissing, parse_conflicts, redact,  # noqa: E402
                              validate_ref, validate_url)
from ndx.security import SecretStore  # noqa: E402


class FakeConfig:
    def __init__(self, **kw):
        self.d = dict(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class GitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        home = self.t / "home"
        home.mkdir()
        self._saved = {k: os.environ.get(k) for k in ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
                                                      "NDX_GITHUB_TOKEN")}
        os.environ.update({"HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        os.environ.pop("NDX_GITHUB_TOKEN", None)
        self.audit = []
        self.cfg = FakeConfig(**{"git.user_name": "Ada Dev", "git.user_email": "ada@example.org"})
        self.repo = self.t / "repo"
        self.g = self.client(self.repo)
        self.g.init(branch="main")

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def client(self, path, **kw):
        kw.setdefault("config", self.cfg)
        kw.setdefault("audit", lambda *a, **k: self.audit.append((a, k)))
        return GitClient(path, **kw)

    def write(self, name, text, repo=None):
        p = Path(repo or self.repo) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit_file(self, name="a.txt", text="one\n", msg="first", g=None):
        g = g or self.g
        self.write(name, text, g.path)
        self.assertTrue(g.add([name])["ok"])
        r = g.commit(msg)
        self.assertTrue(r["ok"], r)


class TestBasics(GitCase):
    def test_init_status_add_commit_log(self):
        self.assertTrue(self.g.is_repo())
        self.write("a.txt", "hi\n")
        st = self.g.status()
        self.assertTrue(st["ok"])
        self.assertEqual(len(st["changes"]), 1)
        self.assertEqual(st["conflicts"], [])
        self.assertTrue(self.g.add_all()["ok"])
        self.assertTrue(self.g.commit("first")["ok"])
        self.assertEqual(self.g.log(5)[0].split(" ", 1)[1], "first")
        e = self.g.log_entries(1)[0]
        self.assertEqual((e["author"], e["email"]), ("Ada Dev", "ada@example.org"))
        self.assertEqual(self.g.current_branch(), "main")

    def test_backward_compatible_api(self):
        self.assertTrue(GitClient.available())
        self.assertTrue(self.g.available())
        self.assertIn("branch", self.g.status())

    def test_commit_needs_message_and_changes(self):
        with self.assertRaises(ValidationError):
            self.g.commit("  ")
        r = self.g.commit("nothing")
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])

    def test_diff_modes(self):
        self.commit_file()
        self.write("a.txt", "one\ntwo\n")
        d = self.g.diff("a.txt")
        self.assertTrue(d["ok"])
        self.assertIn("+two", d["diff"])
        self.assertEqual(self.g.diff(staged=True)["diff"], "")
        self.g.add(["a.txt"])
        self.assertIn("+two", self.g.diff(staged=True)["diff"])
        self.assertIn("+two", self.g.diff(ref="HEAD")["diff"])
        with self.assertRaises(ValidationError):
            self.g.diff("../outside")

    def test_stash(self):
        self.commit_file()
        self.write("a.txt", "changed\n")
        self.assertTrue(self.g.stash("wip")["ok"])
        self.assertEqual(len(self.g.stash_list()), 1)
        self.assertTrue(self.g.stash_pop()["ok"])

    def test_restore_file(self):
        self.commit_file()
        self.write("a.txt", "oops\n")
        self.assertTrue(self.g.restore("a.txt")["ok"])
        self.assertEqual((self.repo / "a.txt").read_text(), "one\n")


class TestIdentity(GitCase):
    def test_no_fictional_identity(self):
        g = self.client(self.repo, config=FakeConfig())
        self.write("b.txt", "x\n")
        g.add(["b.txt"])
        with self.assertRaises(GitIdentityMissing):
            g.commit("no identity")
        self.assertTrue(issubclass(GitIdentityMissing, NDXError))
        # nothing was committed
        self.assertEqual(self.g.log(3), [])

    def test_set_identity_validates_and_persists(self):
        cfg = FakeConfig()
        g = self.client(self.repo, config=cfg)
        with self.assertRaises(ValidationError):
            g.set_identity("", "bad")
        g.set_identity("Grace", "grace@example.org")
        self.assertEqual(cfg.get("git.user_email"), "grace@example.org")
        self.write("c.txt", "x\n")
        g.add_all()
        self.assertTrue(g.commit("by grace")["ok"])
        self.assertEqual(g.log_entries(1)[0]["author"], "Grace")

    def test_existing_git_config_is_used_not_replaced(self):
        subprocess.run(["git", "config", "--file", os.environ["GIT_CONFIG_GLOBAL"], "user.name", "Global Person"], check=True)
        subprocess.run(["git", "config", "--file", os.environ["GIT_CONFIG_GLOBAL"], "user.email", "g@example.org"], check=True)
        g = self.client(self.repo, config=FakeConfig())
        self.write("d.txt", "x\n")
        g.add_all()
        self.assertTrue(g.commit("uses global")["ok"])
        self.assertEqual(g.log_entries(1)[0]["author"], "Global Person")


class TestBranchesMerge(GitCase):
    def test_branch_checkout_merge(self):
        self.commit_file()
        self.assertTrue(self.g.create_branch("feature", checkout=True)["ok"])
        self.commit_file("f.txt", "f\n", "feat")
        self.assertTrue(self.g.checkout("main")["ok"])
        names = [b["name"] for b in self.g.branches()]
        self.assertEqual(sorted(names), ["feature", "main"])
        self.assertTrue(self.g.merge("feature", no_ff=True)["ok"])
        self.assertTrue((self.repo / "f.txt").exists())
        self.assertTrue(self.g.rename_branch("feature", "feat2")["ok"])
        self.assertTrue(self.g.delete_branch("feat2")["ok"])
        self.assertTrue(self.g.checkout("topic", create=True)["ok"])

    def test_conflict_detect_inspect_resolve(self):
        self.commit_file("c.txt", "base\n", "base")
        self.g.create_branch("other", checkout=True)
        self.commit_file("c.txt", "other side\n", "other")
        self.g.checkout("main")
        self.commit_file("c.txt", "main side\n", "main")
        r = self.g.merge("other")
        self.assertFalse(r["ok"])
        self.assertEqual(r["conflicts"], ["c.txt"])
        self.assertEqual(self.g.status()["conflicts"], ["c.txt"])
        hunks = self.g.conflict_hunks("c.txt")
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0]["ours"], ["main side"])
        self.assertEqual(hunks[0]["theirs"], ["other side"])
        self.assertEqual(hunks[0]["theirs_label"], "other")
        # manual resolution refused while markers remain
        self.assertFalse(self.g.resolve_conflict("c.txt", "manual")["ok"])
        self.assertTrue(self.g.resolve_conflict("c.txt", "theirs")["ok"])
        self.assertEqual((self.repo / "c.txt").read_text(), "other side\n")
        self.assertEqual(self.g.conflicts(), [])
        self.assertTrue(self.g.commit("merge resolved")["ok"])

    def test_merge_abort(self):
        self.commit_file("c.txt", "base\n", "base")
        self.g.create_branch("o", checkout=True)
        self.commit_file("c.txt", "o\n", "o")
        self.g.checkout("main")
        self.commit_file("c.txt", "m\n", "m")
        self.assertFalse(self.g.merge("o")["ok"])
        self.assertTrue(self.g.merge_abort()["ok"])
        self.assertEqual(self.g.conflicts(), [])

    def test_parse_conflicts_diff3(self):
        text = "x\n<<<<<<< HEAD\na\n||||||| base\nb\n=======\nc\n>>>>>>> topic\ny\n"
        h = parse_conflicts(text)[0]
        self.assertEqual((h["ours"], h["base"], h["theirs"]), (["a"], ["b"], ["c"]))
        self.assertEqual((h["start"], h["end"]), (2, 8))


class TestRemotes(GitCase):
    def setUp(self):
        super().setUp()
        self.bare = self.t / "origin.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.bare)], check=True, capture_output=True)

    def test_remotes_crud(self):
        self.assertTrue(self.g.remote_add("origin", str(self.bare))["ok"])
        self.assertEqual(self.g.remotes()[0]["name"], "origin")
        other = self.t / "other.git"
        subprocess.run(["git", "init", "--bare", str(other)], check=True, capture_output=True)
        self.assertTrue(self.g.remote_set_url("origin", str(other))["ok"])
        self.assertIn("other.git", self.g.remotes()[0]["url"])
        self.assertTrue(self.g.remote_remove("origin")["ok"])
        self.assertEqual(self.g.remotes(), [])

    def test_push_clone_fetch_pull(self):
        self.commit_file()
        self.g.remote_add("origin", str(self.bare))
        self.assertTrue(self.g.push("origin", "main", set_upstream=True)["ok"])
        clone = self.client(self.t / "clone")
        r = clone.clone(str(self.bare), self.t / "clone")
        self.assertTrue(r["ok"], r)
        self.assertEqual(clone.log(3)[0].split(" ", 1)[1], "first")
        # new commit upstream, then fetch + pull in the clone
        self.commit_file("n.txt", "n\n", "second")
        self.g.push("origin", "main")
        self.assertTrue(clone.fetch("origin")["ok"])
        self.assertEqual(clone.status()["behind"], 1)
        self.assertTrue(clone.pull("origin", "main")["ok"])
        self.assertTrue((self.t / "clone" / "n.txt").exists())

    def test_failed_push_reports_error(self):
        self.commit_file()
        self.g.remote_add("origin", str(self.t / "does-not-exist.git"))
        r = self.g.push("origin", "main")
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])


class TestGitignore(GitCase):
    def test_editor(self):
        self.assertEqual(self.g.gitignore_patterns(), [])
        self.assertTrue(self.g.gitignore_add("*.pyc"))
        self.assertFalse(self.g.gitignore_add("*.pyc"))
        self.g.gitignore_add("build/")
        self.assertEqual(self.g.gitignore_patterns(), ["*.pyc", "build/"])
        self.assertTrue(self.g.gitignore_remove("*.pyc"))
        self.assertFalse(self.g.gitignore_remove("nope"))
        self.assertEqual(self.g.gitignore_text(), "build/\n")
        self.write("x.o", "")
        self.write("build/out", "")
        self.assertEqual(self.g.status()["changes"], ["?? .gitignore", "?? x.o"])
        self.assertFalse(list(self.repo.glob(".gitignore.tmp")))

    def test_confinement(self):
        with self.assertRaises(ValidationError):
            self.g.gitignore_path("../..")
        outside = self.t / "outside"
        outside.mkdir()
        (self.repo / "sub").mkdir()
        os.symlink(outside / "ign", self.repo / "sub" / ".gitignore")
        with self.assertRaises(ValidationError):
            self.g.gitignore_path("sub")
        with self.assertRaises(ValidationError):
            self.g.gitignore_add("a\nb")


class TestInjection(GitCase):
    def test_bad_refs_rejected(self):
        for bad in ("--upload-pack=touch /tmp/pwn", "-x", "a..b", "has space", "a:b", "x.lock", "", "/abs", "a@{1}"):
            with self.assertRaises(ValidationError, msg=bad):
                validate_ref(bad)
            with self.assertRaises(ValidationError, msg=bad):
                self.g.create_branch(bad)
        with self.assertRaises(ValidationError):
            self.g.checkout("--orphan")
        with self.assertRaises(ValidationError):
            self.g.merge("--abort")

    def test_bad_urls_rejected(self):
        for bad in ("ext::sh -c touch /tmp/pwn", "--upload-pack=x", "-oProxyCommand=x", "ftp://x/y", "ssh://-oProxy/x",
                    "nonexistent-local-thing", " https://x/y", "fd::17"):
            with self.assertRaises(ValidationError, msg=bad):
                validate_url(bad)
            with self.assertRaises(ValidationError, msg=bad):
                self.g.remote_add("evil", bad)
        for good in ("https://github.com/a/b.git", "git@github.com:a/b.git", "ssh://git@h/x", "file:///tmp/x", str(self.t)):
            validate_url(good)
        with self.assertRaises(ValidationError):
            self.g.remote_add("--bad", "https://x/y")
        self.assertEqual(self.g.remotes(), [])

    def test_paths_cannot_escape_or_be_options(self):
        for bad in ("../x", "/etc/passwd", ":(top)x", "a\0b"):
            with self.assertRaises(ValidationError, msg=bad):
                self.g.add([bad])
        self.write("-weird", "x\n")
        self.assertTrue(self.g.add(["-weird"])["ok"])      # a file named like an option is passed after `--`

    def test_ext_transport_blocked_by_environment(self):
        env = self.g._env()
        self.assertEqual(env["GIT_ALLOW_PROTOCOL"], "https:ssh:git:file")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")


class TestAuthAndRedaction(GitCase):
    def make_wrapper(self):
        rec = self.t / "rec.txt"
        w = self.t / "fakegit.sh"
        w.write_text('#!/bin/sh\n'
                     'echo "token_in_env=${NDX_GIT_TOKEN:+yes}" > "%s"\n'
                     'echo "askpass=$GIT_ASKPASS" >> "%s"\n'
                     'if [ -x "$GIT_ASKPASS" ]; then echo "askpass_exec=yes" >> "%s"; fi\n'
                     'echo "argv=$*" >> "%s"\n'
                     'echo "fatal: unable to access https://user:$NDX_GIT_TOKEN@github.com/o/r/: $NDX_GIT_TOKEN" >&2\n'
                     'exit 1\n' % (rec, rec, rec, rec))
        w.chmod(0o755)
        return w, rec

    def test_token_only_in_child_and_redacted(self):
        secret = "ghp_SUPERSECRET1234567890"
        store = SecretStore(self.t / "secrets.json")
        store.set("github_token", secret)
        self.assertEqual(stat.S_IMODE(os.stat(store.path).st_mode), 0o600)
        w, rec = self.make_wrapper()

        class TC:
            def resolve_path(self_, name):
                return str(w)

        g = self.client(self.repo, toolchain=TC(), secrets=store)
        g._remote_url = lambda remote: "https://github.com/o/r.git"       # no real remote needed
        r = g.push("origin", "main")
        self.assertFalse(r["ok"])
        blob = repr(r) + repr(self.audit)
        self.assertNotIn(secret, blob)
        self.assertIn("***", r["stderr"])
        info = rec.read_text()
        self.assertIn("token_in_env=yes", info)
        self.assertIn("askpass_exec=yes", info)
        self.assertNotIn(secret, info.split("argv=")[1])                 # never on the command line
        ask = [l for l in info.splitlines() if l.startswith("askpass=")][0][8:]
        self.assertFalse(os.path.exists(ask), "askpass helper must be removed afterwards")
        self.assertFalse(os.path.exists(os.path.dirname(ask)))
        self.assertNotIn("NDX_GIT_TOKEN", os.environ)                      # parent environment untouched

    def test_token_not_sent_to_other_hosts_or_http(self):
        store = SecretStore(self.t / "s.json")
        store.set("github_token", "ghp_ZZZZZZZZZZZZZZZZ")
        w, rec = self.make_wrapper()

        class TC:
            def resolve_path(self_, name):
                return str(w)

        g = self.client(self.repo, toolchain=TC(), secrets=store)
        for url in ("https://evil.example.com/x.git", "http://github.com/x.git", "git@github.com:o/r.git"):
            g._remote_url = lambda remote, u=url: u
            g.fetch("origin")
            self.assertIn("token_in_env=\n", rec.read_text() + "\n", url)
        # explicit opt-in host
        g.config = FakeConfig(**{"git.auth_hosts": ["git.example.com"]})
        g._remote_url = lambda remote: "https://git.example.com/x.git"
        g.fetch("origin")
        self.assertIn("token_in_env=yes", rec.read_text())

    def test_local_push_leaks_nothing(self):
        bare = self.t / "o.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
        store = SecretStore(self.t / "s2.json")
        store.set("github_token", "ghp_LOCALTOKEN987654321")
        g = self.client(self.repo, secrets=store)
        self.commit_file(g=g)
        g.remote_add("origin", str(bare))
        r = g.push("origin", "main")
        self.assertTrue(r["ok"], r)
        self.assertNotIn("ghp_LOCALTOKEN987654321", repr(r) + repr(self.audit))

    def test_redact_helper(self):
        self.assertEqual(redact("x tok123456 y", ["tok123456"]), "x *** y")
        self.assertEqual(redact("https://user:pw@host/a"), "https://***@host/a")
        self.assertEqual(redact("clone https://ghp_abc@github.com/x"), "clone https://***@github.com/x")
        self.assertIsNone(redact(None))

    def test_secret_store(self):
        s = SecretStore(self.t / "sec" / "s.json", environ={"NDX_GITHUB_TOKEN": "envtoken1234"})
        self.assertEqual(s.get("github_token"), "envtoken1234")
        self.assertEqual(s.source("github_token"), "env")
        s.set("github_token", "stored1234")
        self.assertEqual(s.get("github_token"), "stored1234")
        self.assertEqual(s.names(), ["github_token"])
        self.assertNotIn("stored1234", repr(s))
        self.assertTrue(s.delete("github_token"))
        self.assertEqual(s.source("github_token"), "env")
        with self.assertRaises(ValidationError):
            s.set("../bad", "x")
        with self.assertRaises(ValidationError):
            s.set("ok", "multi\nline")


class TestAvailability(GitCase):
    def test_ensure_available_without_git(self):
        class TC:
            def resolve_path(self_, name):
                return None
        g = self.client(self.repo, toolchain=TC())
        self.assertFalse(g.available())
        with self.assertRaises(ToolUnavailable) as cm:
            g.ensure_available()
        self.assertIn("ndx tool install git", str(cm.exception))
        with self.assertRaises(ToolUnavailable):
            g.status()

    def test_ensure_available_offers_install(self):
        calls = []

        class TC:
            installed = False

            def resolve_path(self_, name):
                return "/usr/bin/git" if self_.installed else None

            def install(self_, tool, confirm=True):
                calls.append((tool, confirm))
                self_.installed = True

        g = self.client(self.repo, toolchain=TC())
        self.assertTrue(g.ensure_available(install=True))
        self.assertEqual(calls, [("git", True)])

        class Bad(TC):
            def install(self_, tool, confirm=True):
                raise ToolUnavailable("no build for this CPU")
        with self.assertRaises(ToolUnavailable) as cm:
            self.client(self.repo, toolchain=Bad()).ensure_available(install=True)
        self.assertIn("no build for this CPU", str(cm.exception))

    def test_custom_runner_is_used(self):
        seen = []

        class R:
            returncode, stdout, stderr = 0, "true\n", ""

        def runner(argv, cwd, env, timeout):
            seen.append(argv)
            return R()
        g = self.client(self.repo, runner=runner, environment="ubuntu")
        self.assertTrue(g.is_repo())
        self.assertEqual(seen[0][0], "git")


class TestCli(GitCase):
    def make_cli(self):
        from ndx.core.registry import ServiceRegistry

        class Core:
            pass
        core = Core()
        core.services = ServiceRegistry()
        core.services.register("config", self.cfg)
        core.services.register("secrets", SecretStore(self.t / "cli-secrets.json"))
        core.services.register("audit", lambda *a, **k: self.audit.append((a, k)))
        core.paths = type("P", (), {"projects": self.t})()
        return GitCli(core)

    def run_cli(self, *args):
        buf = []
        rc = self.make_cli().run(list(args), buf.append)
        return rc, "".join(buf)

    def test_cli_flow(self):
        r = str(self.repo)
        self.write("a.txt", "x\n")
        self.assertEqual(self.run_cli("-C", r, "add", "-A")[0], 0)
        self.assertEqual(self.run_cli("-C", r, "commit", "-m", "from cli")[0], 0)
        rc, out = self.run_cli("-C", r, "log")
        self.assertIn("from cli", out)
        self.assertIn("main", self.run_cli("-C", r, "status")[1])
        self.assertEqual(self.run_cli("-C", r, "ignore", "add", "*.log")[0], 0)
        self.assertIn("*.log", self.run_cli("-C", r, "ignore")[1])
        rc, out = self.run_cli("-C", r, "branch", "new", "--bad")
        self.assertEqual(rc, 1)
        self.assertIn("error", out)
        self.assertEqual(self.run_cli("bogus")[0], 2)

    def test_cli_auth(self):
        self.assertIn("not set", self.run_cli("auth", "status")[1])
        os.environ["TMP_TOK_VAR"] = "ghp_clitoken123456"
        self.assertEqual(self.run_cli("auth", "set", "--from-env", "TMP_TOK_VAR")[0], 0)
        self.assertIn("stored", self.run_cli("auth", "status")[1])
        self.assertNotIn("ghp_clitoken123456", self.run_cli("auth", "status")[1])
        self.assertEqual(self.run_cli("auth", "clear")[0], 0)
        del os.environ["TMP_TOK_VAR"]

    def test_cli_identity(self):
        rc, out = self.run_cli("identity", "set", "Linus T", "l@example.org")
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg.get("git.user_name"), "Linus T")


if __name__ == "__main__":
    unittest.main()
