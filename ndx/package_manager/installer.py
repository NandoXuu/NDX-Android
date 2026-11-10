"""Package manager: validated, consent-gated, rollback-capable installs.

Security model: installing a package NEVER executes any code from it. Python
code in a plugin only runs later, after the plugin is trusted and the user
has explicitly approved code execution for it (plugin_system.loader)."""
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from .. import __version__
from ..core.errors import ValidationError, NDXError
from ..core.registry import Module
from ..security.safepath import safe_extract_zip
from . import github
from .manifest import MANIFEST_NAME, parse_manifest, validate_manifest
from .store import PluginStore, tree_hash


class PackageManager(Module):
    name = "packages"
    title = "Package Manager"

    def __init__(self, paths, bus, consent=None, audit=None, ndx_version=__version__):
        self.paths, self.bus, self.consent = paths, bus, consent
        self.audit = audit or (lambda *a, **k: None)
        self.ndx_version = ndx_version
        self.store = PluginStore(paths.plugins, paths.data)

    # ---- inspection
    @staticmethod
    def _find_root(staging):
        staging = Path(staging)
        if (staging / MANIFEST_NAME).exists():
            return staging
        subs = [d for d in staging.iterdir() if d.is_dir()]
        if len(subs) == 1 and (subs[0] / MANIFEST_NAME).exists():
            return subs[0]
        raise ValidationError("package has no %s at its root" % MANIFEST_NAME)

    def inspect_dir(self, root):
        raw = parse_manifest((Path(root) / MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest, problems = validate_manifest(raw, self.ndx_version)
        if manifest.has_code:
            entry = (Path(root) / manifest.entry["module"]).resolve()
            if Path(root).resolve() not in entry.parents or not entry.is_file():
                problems.append("[entry].module file is missing from the package")
        return manifest, problems

    def check_dependencies(self, manifest):
        missing = []
        for dep, spec in manifest.dependencies.items():
            e = self.store.get(dep)
            if not e or not SpecifierSet(spec).contains(Version(e["version"]), prereleases=True):
                missing.append("%s%s" % (dep, spec))
        return missing

    # ---- install
    def install_path(self, path, source="local", expected_sha256=None, progress=None):
        path = Path(path)
        with tempfile.TemporaryDirectory(dir=self.paths.cache) as tmp:
            if path.is_dir():
                staging = Path(tmp) / "pkg"
                shutil.copytree(path, staging, symlinks=False)
            else:
                if expected_sha256:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest != expected_sha256.lower():
                        raise ValidationError("checksum mismatch for %s" % path.name)
                if not zipfile.is_zipfile(path):
                    raise ValidationError("not a zip package: %s" % path.name)
                staging = safe_extract_zip(path, Path(tmp) / "pkg")
            root = self._find_root(staging)
            manifest, problems = self.inspect_dir(root)
            if problems:
                raise ValidationError("package validation failed", problems)
            missing = self.check_dependencies(manifest)
            if missing:
                raise ValidationError("missing dependencies: " + ", ".join(missing), missing)
            cur = self.store.get(manifest.id)
            if cur and Version(cur["version"]) == Version(manifest.version) and cur.get("source") == source:
                raise ValidationError("%s %s is already installed" % (manifest.id, manifest.version))
            self._confirm(manifest, source)
            dest = self.store.version_dir(manifest.id, manifest.version)
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root, dest, symlinks=False)
            for f in dest.rglob("*"):
                if f.is_file():
                    os.chmod(f, 0o644)
        self.store.commit(manifest.id, manifest.version, {
            "name": manifest.name, "category": manifest.category, "source": source,
            "permissions": manifest.permissions, "has_code": manifest.has_code,
            "tree_sha256": tree_hash(dest), "code_approved": False,
            "enabled": cur.get("enabled", True) if cur else True})
        self.audit("plugin.install", id=manifest.id, version=manifest.version, source=source)
        self.bus.publish("plugins.changed", {"id": manifest.id}, "packages")
        return manifest

    def _confirm(self, manifest, source):
        details = ["%s %s by %s" % (manifest.name, manifest.version, manifest.author),
                   "Category: " + manifest.category, "Source: " + source,
                   "Requested permissions: " + (", ".join(manifest.permissions) or "none"),
                   "Contains Python code: " + ("YES (will NOT run until you approve it)" if manifest.has_code else "no")]
        if self.consent is None or not self.consent.request("Install plugin?", "Third-party code is untrusted.", details):
            raise NDXError("installation cancelled (consent not given)")

    def install_github(self, url, version=None, progress=None):
        owner, repo = github.parse_repo_url(url)
        rel = github.resolve_release(owner, repo, version)
        with tempfile.TemporaryDirectory(dir=self.paths.cache) as tmp:
            z = Path(tmp) / "pkg.zip"
            github.download(rel["url"], z, rel.get("sha256"), progress=progress)
            note = rel["kind"] + (" (checksum verified)" if rel.get("sha256") else " (no checksum published)")
            return self.install_path(z, source="github:%s/%s [%s]" % (owner, repo, note))

    def update(self, pid):
        e = self.store.get(pid)
        if not e or not str(e.get("source", "")).startswith("github:"):
            raise NDXError("%s was not installed from GitHub; reinstall from a newer package" % pid)
        repo = e["source"].split(":", 1)[1].split(" ", 1)[0]
        return self.install_github("https://github.com/" + repo)

    def uninstall(self, pid):
        if not self.store.get(pid):
            raise NDXError("%s is not installed" % pid)
        dependents = [k for k, v in self.store.all().items()
                      if k != pid and pid in self._deps_of(k, v)]
        if dependents:
            raise NDXError("cannot uninstall: required by " + ", ".join(dependents))
        self.store.remove(pid)
        self.audit("plugin.uninstall", id=pid)
        self.bus.publish("plugins.changed", {"id": pid}, "packages")

    def _deps_of(self, pid, entry):
        try:
            m, _ = self.inspect_dir(self.store.version_dir(pid, entry["version"]))
            return list(m.dependencies)
        except Exception:
            return []

    def rollback(self, pid):
        prev = self.store.rollback(pid)
        if not prev:
            raise NDXError("no previous version of %s to roll back to" % pid)
        self.bus.publish("plugins.changed", {"id": pid}, "packages")
        return prev

    # ---- listing / health
    def list_installed(self):
        return self.store.all()

    def verify(self, pid):
        e = self.store.get(pid)
        d = self.store.version_dir(pid, e["version"])
        return d.exists() and tree_hash(d) == e.get("tree_sha256")

    def search_local(self, query):
        q = query.lower()
        return [{"id": k, "name": v.get("name", ""), "version": v["version"]}
                for k, v in self.store.all().items() if q in k or q in v.get("name", "").lower()]

    def search(self, query, remote=True):
        res = {"installed": self.search_local(query), "github": [], "error": ""}
        if remote:
            try:
                res["github"] = github.search_topic(query)
            except NDXError as e:
                res["error"] = str(e)
        return res
