"""Tool manifest format and validation.

A manifest describes ONE external tool: where its executable ends up, which
architectures/platforms it supports, how it can be installed and how it is
verified. Manifests are plain JSON (built-in catalog in catalog.py, or user
files in <ndx_home>/data/toolchain/manifests/*.json) and are always validated
before use: an invalid manifest is rejected, never half-applied.

Example (download method, pinned checksum)::

    {"id": "proot", "name": "PRoot", "version": "5.4.0",
     "platform": ["android", "linux"], "architectures": ["arm64-v8a"],
     "executable": "bin/proot", "required": false,
     "capabilities": ["linux_environment"],
     "allowed_hosts": ["github.com"],
     "install": [{"method": "download", "sources": {"arm64-v8a": {
         "url": "https://github.com/.../proot-aarch64.tar.gz", "archive": "tar.gz",
         "sha256": "<64 hex chars>", "size": 1048576, "installed_size": 2097152}}}]}
"""
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..core.errors import ValidationError

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,62}$")
CMD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ARCHITECTURES = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
PLATFORMS = ("android", "linux")
METHODS = ("download", "native_lib", "linux_package", "termux_package", "manual")
ARCHIVES = ("raw", "zip", "tar", "tar.gz", "tar.xz", "tar.bz2")
EXE_FORMATS = ("elf", "script")
PACKAGE_MANAGERS = ("apt", "apk", "pacman")
KNOWN_KEYS = {"id", "name", "version", "description", "platform", "architectures", "executable", "exe_format",
              "provides", "required", "capabilities", "requires", "install", "version_args", "version_regex",
              "allowed_hosts", "env", "homepage", "license", "min_android_api", "size_estimate", "notes",
              "source_status"}


@dataclass
class Source:
    url: str
    archive: str = "raw"
    sha256: str = ""
    sha256_url: str = ""          # sidecar checksum file published next to the artifact
    size: int = 0                 # download size, bytes (0 = unknown)
    installed_size: int = 0       # size on disk after extraction (0 = unknown)
    member: str = ""              # exact path of the executable inside the archive ("" = search by basename)

    @property
    def integrity(self):
        """pinned | sidecar | unverified"""
        return "pinned" if self.sha256 else ("sidecar" if self.sha256_url else "unverified")


@dataclass
class ToolManifest:
    id: str
    name: str
    version: str
    executable: str
    description: str = ""
    platform: list = field(default_factory=lambda: ["android", "linux"])
    architectures: list = field(default_factory=lambda: list(ARCHITECTURES))
    exe_format: str = "elf"
    provides: list = field(default_factory=list)       # command names to expose (default: basename of executable)
    required: bool = False
    capabilities: list = field(default_factory=list)
    requires: list = field(default_factory=list)       # other tool ids
    install: list = field(default_factory=list)        # ordered install methods (dicts)
    version_args: list = field(default_factory=lambda: ["--version"])
    version_regex: str = r"(\d+(?:\.\d+)+)"
    allowed_hosts: list = field(default_factory=list)
    env: dict = field(default_factory=dict)            # extra env: values may use {dir} {tmp}
    homepage: str = ""
    license: str = ""
    min_android_api: int = 0
    size_estimate: int = 0                             # typical installed size, bytes
    notes: str = ""
    source_status: str = "unverified"                  # verified | unverified (URL/package names not checked)
    origin: str = "builtin"                            # builtin | user

    def commands(self):
        return self.provides or [posixpath.basename(self.executable)]

    def method(self, name):
        for m in self.install:
            if m.get("method") == name:
                return m
        return None

    def sources_for(self, arch):
        m = self.method("download")
        if not m:
            return None
        s = (m.get("sources") or {}).get(arch)
        return Source(**s) if s else None

    def to_dict(self):
        d = {k: getattr(self, k) for k in KNOWN_KEYS if hasattr(self, k)}
        return d


def _host_ok(host, allowed):
    host = (host or "").lower()
    return any(host == a or host.endswith("." + a) for a in allowed)


def validate_manifest(data, origin="builtin"):
    """Return a ToolManifest or raise ValidationError(problems=[...])."""
    p = []
    if not isinstance(data, dict):
        raise ValidationError("manifest must be a JSON object", ["not an object"])
    for k in data:
        if k not in KNOWN_KEYS:
            p.append("unknown key: %s" % k)
    tid = data.get("id", "")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        p.append("id must match %s" % ID_RE.pattern)
    for req in ("name", "version", "executable"):
        if not isinstance(data.get(req), str) or not data.get(req, "").strip():
            p.append("missing required field: %s" % req)
    ver = data.get("version", "")
    if isinstance(ver, str) and ver:
        try:
            from packaging.version import Version
            Version(ver)
        except Exception:
            p.append("version is not a valid version string: %r" % ver)
    exe = data.get("executable", "")
    if isinstance(exe, str) and exe:
        norm = posixpath.normpath(exe)
        if exe.startswith("/") or norm.startswith("..") or "\\" in exe or "\0" in exe:
            p.append("executable must be a relative path inside the tool directory")
    plat = data.get("platform", ["android", "linux"])
    if not isinstance(plat, list) or not plat or any(x not in PLATFORMS for x in plat):
        p.append("platform must be a non-empty subset of %s" % (PLATFORMS,))
    arch = data.get("architectures", list(ARCHITECTURES))
    if not isinstance(arch, list) or not arch or any(x not in ARCHITECTURES for x in arch):
        p.append("architectures must be a non-empty subset of %s" % (ARCHITECTURES,))
    if data.get("exe_format", "elf") not in EXE_FORMATS:
        p.append("exe_format must be one of %s" % (EXE_FORMATS,))
    for name in data.get("provides", []) or []:
        if not isinstance(name, str) or not CMD_RE.match(name):
            p.append("invalid command name in provides: %r" % (name,))
    for dep in data.get("requires", []) or []:
        if not isinstance(dep, str) or not ID_RE.match(dep):
            p.append("invalid dependency id: %r" % (dep,))
    if tid and tid in (data.get("requires") or []):
        p.append("a tool cannot require itself")
    va = data.get("version_args", ["--version"])
    if not isinstance(va, list) or any(not isinstance(x, str) for x in va):
        p.append("version_args must be a list of strings")
    vr = data.get("version_regex", "")
    if vr:
        try:
            re.compile(vr)
        except re.error as e:
            p.append("version_regex is not a valid regex: %s" % e)
    hosts = data.get("allowed_hosts", []) or []
    if not isinstance(hosts, list) or any(not isinstance(h, str) or "/" in h for h in hosts):
        p.append("allowed_hosts must be a list of host names")
        hosts = []
    env = data.get("env", {}) or {}
    if not isinstance(env, dict) or any(not isinstance(k, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k)
                                        or not isinstance(v, str) for k, v in env.items()):
        p.append("env must map valid variable names to strings")
    methods = data.get("install", []) or []
    if not isinstance(methods, list):
        p.append("install must be a list")
        methods = []
    for i, m in enumerate(methods):
        where = "install[%d]" % i
        if not isinstance(m, dict) or m.get("method") not in METHODS:
            p.append("%s: method must be one of %s" % (where, METHODS))
            continue
        kind = m["method"]
        if kind == "download":
            srcs = m.get("sources")
            if not isinstance(srcs, dict) or not srcs:
                p.append("%s: download needs a 'sources' map arch -> source" % where)
                continue
            if not hosts:
                p.append("%s: download needs allowed_hosts" % where)
            for a, s in srcs.items():
                if a not in ARCHITECTURES:
                    p.append("%s: unknown architecture %s" % (where, a))
                    continue
                if not isinstance(s, dict) or "url" not in s:
                    p.append("%s.%s: source needs a url" % (where, a))
                    continue
                extra = set(s) - set(Source.__dataclass_fields__)
                if extra:
                    p.append("%s.%s: unknown source keys %s" % (where, a, sorted(extra)))
                    continue
                u = urlparse(s["url"])
                if u.scheme != "https":
                    p.append("%s.%s: only https URLs are allowed" % (where, a))
                elif not _host_ok(u.hostname, hosts):
                    p.append("%s.%s: host %s is not in allowed_hosts" % (where, a, u.hostname))
                if s.get("sha256") and not SHA256_RE.match(str(s["sha256"]).lower()):
                    p.append("%s.%s: sha256 must be 64 hex chars" % (where, a))
                if s.get("sha256_url"):
                    su = urlparse(s["sha256_url"])
                    if su.scheme != "https" or not _host_ok(su.hostname, hosts):
                        p.append("%s.%s: sha256_url must be https on an allowed host" % (where, a))
                if s.get("archive", "raw") not in ARCHIVES:
                    p.append("%s.%s: archive must be one of %s" % (where, a, ARCHIVES))
                mem = s.get("member", "")
                if mem and (mem.startswith("/") or posixpath.normpath(mem).startswith("..")):
                    p.append("%s.%s: member must be a relative path" % (where, a))
        elif kind == "linux_package":
            pk = m.get("packages")
            if not isinstance(pk, dict) or not pk or any(k not in PACKAGE_MANAGERS or not isinstance(v, str)
                                                       or not re.match(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$", v)
                                                       for k, v in pk.items()):
                p.append("%s: linux_package needs 'packages' mapping %s -> valid package name" % (where, PACKAGE_MANAGERS))
        elif kind == "termux_package":
            if not isinstance(m.get("package"), str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$", m["package"]):
                p.append("%s: termux_package needs a valid 'package' name" % where)
        elif kind == "native_lib":
            lib = m.get("lib", "")
            if not isinstance(lib, str) or not re.match(r"^lib[A-Za-z0-9._+-]+\.so$", lib):
                p.append("%s: native_lib needs 'lib' like libproot.so" % where)
    if p:
        raise ValidationError("invalid tool manifest%s: %d problem(s)" % (" '%s'" % tid if tid else "", len(p)), p)
    fields = {k: v for k, v in data.items() if k in ToolManifest.__dataclass_fields__}
    fields["origin"] = origin
    return ToolManifest(**fields)
