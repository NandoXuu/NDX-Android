"""ndx-plugin.toml parsing and validation."""
import re
from dataclasses import dataclass, field

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib

from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import Version, InvalidVersion

from ..core.errors import ValidationError
from ..security.permissions import CAPABILITIES

MANIFEST_NAME = "ndx-plugin.toml"
CATEGORIES = ["language", "compiler", "framework", "library", "gui-toolkit", "debugger",
              "language-server", "theme", "integration", "shell-tool", "runtime-environment"]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    author: str
    description: str
    ndx_version: str
    category: str
    permissions: list = field(default_factory=list)
    dependencies: dict = field(default_factory=dict)
    entry: dict = field(default_factory=dict)          # {"module": "main.py", "class": "Plugin"}
    language: dict = field(default_factory=dict)
    compiler: dict = field(default_factory=dict)
    theme: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def has_code(self):
        return bool(self.entry.get("module"))


def parse_manifest(text):
    try:
        return tomllib.loads(text)
    except Exception as exc:
        raise ValidationError("ndx-plugin.toml is not valid TOML: %s" % exc)


def validate_manifest(raw, ndx_version):
    """Return (PluginManifest, problems). problems empty == valid."""
    problems = []
    plugin = raw.get("plugin", {}) if isinstance(raw.get("plugin"), dict) else {}
    for key in ("id", "name", "version", "author", "description", "ndx_version"):
        if not isinstance(plugin.get(key), str) or not plugin.get(key).strip():
            problems.append("[plugin].%s is required" % key)
    pid = plugin.get("id", "")
    if pid and not ID_RE.match(pid):
        problems.append("[plugin].id must match %s" % ID_RE.pattern)
    try:
        Version(plugin.get("version", ""))
    except (InvalidVersion, TypeError):
        problems.append("[plugin].version is not a valid version")
    try:
        spec = SpecifierSet(plugin.get("ndx_version", ""))
        if not spec.contains(Version(ndx_version), prereleases=True):
            problems.append("requires NDX %s (running %s)" % (plugin.get("ndx_version"), ndx_version))
    except InvalidSpecifier:
        problems.append("[plugin].ndx_version is not a valid version specifier")
    ptype = raw.get("plugin", {}).get("type", {}) if isinstance(raw.get("plugin"), dict) else {}
    category = ptype.get("category", "") if isinstance(ptype, dict) else ""
    if category not in CATEGORIES:
        problems.append("[plugin.type].category must be one of: %s" % ", ".join(CATEGORIES))
    perms = (raw.get("permissions") or {}).get("request", [])
    if not isinstance(perms, list):
        problems.append("[permissions].request must be a list")
        perms = []
    for c in perms:
        if c not in CAPABILITIES:
            problems.append("unknown permission: %s" % c)
    deps = raw.get("dependencies") or {}
    for dep, req in deps.items():
        try:
            SpecifierSet(req)
        except InvalidSpecifier:
            problems.append("dependency %s has an invalid specifier" % dep)
    entry = raw.get("entry") or {}
    if entry:
        mod = entry.get("module", "")
        if not mod.endswith(".py") or ".." in mod or mod.startswith("/"):
            problems.append("[entry].module must be a relative .py file inside the package")
    m = PluginManifest(
        id=pid, name=plugin.get("name", ""), version=plugin.get("version", ""),
        author=plugin.get("author", ""), description=plugin.get("description", ""),
        ndx_version=plugin.get("ndx_version", ""), category=category, permissions=list(perms),
        dependencies=dict(deps), entry=dict(entry), language=dict(raw.get("language") or {}),
        compiler=dict(raw.get("compiler") or {}), theme=dict(raw.get("theme") or {}),
        features=dict(raw.get("features") or {}), raw=raw)
    return m, problems
