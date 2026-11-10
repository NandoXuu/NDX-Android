"""SecretStore: small named-secret store (e.g. a GitHub token) kept in the app's private data directory.

HONEST SECURITY NOTE: the file is created with mode 0600 inside app-private storage, so other apps cannot read it
on a non-rooted Android device. It is NOT encrypted and NOT hardware-backed (no Android Keystore). Anyone with
access to the app's private storage (root, a device backup if allowed, a malicious plugin running in-process) can
read it. Secrets are never logged, never included in repr(), and never passed on command lines by NDX.

`NDX_GITHUB_TOKEN` in the environment is accepted as a read-only fallback for the name `github_token`."""
import json
import os
import re
import threading
import tempfile

from ..core.errors import ValidationError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ENV_FALLBACK = {"github_token": "NDX_GITHUB_TOKEN"}


class SecretStore:
    def __init__(self, path, environ=None):
        self.path = str(path)
        self._environ = environ if environ is not None else os.environ
        self._lock = threading.RLock()

    def __repr__(self):
        return "<SecretStore %s (%d secrets)>" % (self.path, len(self.names()))

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        parent = os.path.dirname(self.path) or "."
        os.makedirs(parent, exist_ok=True)
        # mkstemp creates a unique file with restrictive permissions and avoids
        # following a pre-existing predictable ".tmp" symlink.
        fd, tmp = tempfile.mkstemp(prefix=".secretstore-", dir=parent)
        try:
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _check(name):
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ValidationError("invalid secret name: %r" % (name,))

    def set(self, name, value):
        self._check(name)
        if not isinstance(value, str) or not value or "\n" in value or "\0" in value:
            raise ValidationError("a secret must be a non-empty single-line string")
        with self._lock:
            data = self._load()
            data[name] = value
            self._save(data)

    def get(self, name, default=None):
        self._check(name)
        with self._lock:
            v = self._load().get(name)
        if v:
            return v
        env = ENV_FALLBACK.get(name)
        if env and self._environ.get(env):
            return self._environ[env]
        return default

    def source(self, name):
        """'store' | 'env' | None: where `name` would be read from (never reveals the value)."""
        self._check(name)
        if self._load().get(name):
            return "store"
        env = ENV_FALLBACK.get(name)
        return "env" if env and self._environ.get(env) else None

    def has(self, name):
        return self.source(name) is not None

    def delete(self, name):
        self._check(name)
        with self._lock:
            data = self._load()
            existed = data.pop(name, None) is not None
            if existed:
                self._save(data)
            return existed

    def names(self):
        with self._lock:
            return sorted(self._load())
