"""Architecture/platform detection, ELF inspection (ToolValidator) and
compatibility checks (ToolCompatibilityChecker).

Why ELF inspection: a desktop-Linux binary (glibc/musl, interpreter
/lib64/ld-linux-*.so) cannot run on Android, and a Termux build expects the
Termux prefix. We read the ELF header instead of trusting file names."""
import os
import platform
import re
import struct
import subprocess
from dataclasses import dataclass, field

from ..core.errors import ValidationError
from ..core.paths import is_android

EM_TO_ARCH = {0x03: "x86", 0x28: "armeabi-v7a", 0x3E: "x86_64", 0xB7: "arm64-v8a"}
ANDROID_INTERPS = ("/system/bin/linker64", "/system/bin/linker", "/apex/com.android.runtime/bin/linker64",
                   "/apex/com.android.runtime/bin/linker", "/system/bin/linker_asan64")
_MACHINE = {"aarch64": "arm64-v8a", "arm64": "arm64-v8a", "armv8l": "armeabi-v7a", "armv7l": "armeabi-v7a",
            "arm": "armeabi-v7a", "x86_64": "x86_64", "amd64": "x86_64", "i686": "x86", "i386": "x86"}


def detect_arch():
    """Primary architecture of this process' userland (override: NDX_ARCH env for tests)."""
    forced = os.environ.get("NDX_ARCH")
    if forced:
        return forced
    arch = _MACHINE.get(platform.machine().lower(), platform.machine().lower())
    if arch == "arm64-v8a" and struct.calcsize("P") == 4:       # 32-bit userland on a 64-bit kernel
        arch = "armeabi-v7a"
    return arch


def supported_abis():
    """ABIs this device can execute, preferred first. Android: android.os.Build.SUPPORTED_ABIS."""
    forced = os.environ.get("NDX_ABIS")
    if forced:
        return [a for a in forced.split(",") if a]
    if is_android():
        try:
            from jnius import autoclass
            return list(autoclass("android.os.Build").SUPPORTED_ABIS)
        except Exception:
            pass
    return [detect_arch()]


def current_platform():
    return "android" if is_android() else ("linux" if platform.system() == "Linux" else platform.system().lower())


def android_sdk():
    if not is_android():
        return 0
    try:
        from jnius import autoclass
        return int(autoclass("android.os.Build$VERSION").SDK_INT)
    except Exception:
        return 0


def read_elf(path):
    """Parse the ELF header + PT_INTERP. Returns None when the file is not ELF."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
            if len(head) < 52 or head[:4] != b"\x7fELF":
                return None
            bits = 64 if head[4] == 2 else 32
            e = "<" if head[5] == 1 else ">"
            e_type, e_machine = struct.unpack_from(e + "HH", head, 16)
            if bits == 64:
                phoff, = struct.unpack_from(e + "Q", head, 32)
                phentsize, phnum = struct.unpack_from(e + "HH", head, 54)
            else:
                phoff, = struct.unpack_from(e + "I", head, 28)
                phentsize, phnum = struct.unpack_from(e + "HH", head, 42)
            interp = None
            for i in range(min(phnum, 64)):
                fh.seek(phoff + i * phentsize)
                ph = fh.read(phentsize)
                if len(ph) < phentsize:
                    break
                if bits == 64:
                    p_type, = struct.unpack_from(e + "I", ph, 0)
                    off, = struct.unpack_from(e + "Q", ph, 8)
                    sz, = struct.unpack_from(e + "Q", ph, 32)
                else:
                    p_type, = struct.unpack_from(e + "I", ph, 0)
                    off, = struct.unpack_from(e + "I", ph, 4)
                    sz, = struct.unpack_from(e + "I", ph, 16)
                if p_type == 3 and 0 < sz < 512:
                    fh.seek(off)
                    interp = fh.read(sz).split(b"\0", 1)[0].decode("utf-8", "replace")
                    break
            return {"bits": bits, "machine": e_machine, "arch": EM_TO_ARCH.get(e_machine, "unknown(0x%x)" % e_machine),
                    "type": {2: "EXEC", 3: "DYN"}.get(e_type, str(e_type)), "interp": interp,
                    "static": interp is None}
    except OSError:
        return None


@dataclass
class CheckResult:
    ok: bool = True
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def fail(self, msg):
        self.ok = False
        self.problems.append(msg)

    def summary(self):
        return "; ".join(self.problems) if self.problems else "ok"


class ToolValidator:
    """Checks that a file is a runnable executable of the expected kind."""

    def __init__(self, target_android=None, abis=None):
        self.target_android = is_android() if target_android is None else target_android
        self.abis = abis

    def validate_file(self, path, exe_format="elf", arch=None):
        r = CheckResult()
        if not os.path.isfile(path):
            r.fail("file does not exist: %s" % path)
            return r
        if os.path.getsize(path) == 0:
            r.fail("file is empty")
            return r
        with open(path, "rb") as fh:
            head = fh.read(4)
        if exe_format == "script":
            if head[:2] != b"#!":
                r.fail("expected a script with a #! line")
            return r
        info = read_elf(path)
        if info is None:
            kind = "an HTML page (bad download?)" if head[:1] == b"<" else \
                   "a script" if head[:2] == b"#!" else "not an ELF executable"
            r.fail("%s: file is %s" % (os.path.basename(path), kind))
            return r
        r.info = info
        abis = self.abis or supported_abis()
        want = arch or (abis[0] if abis else detect_arch())
        if info["arch"] != want and info["arch"] not in abis:
            r.fail("binary is built for %s but this device runs %s" % (info["arch"], "/".join(abis)))
        if self.target_android and info["interp"]:
            if info["interp"] not in ANDROID_INTERPS:
                if "com.termux" in info["interp"]:
                    r.fail("binary is built for the Termux prefix (%s) and cannot run from NDX storage" % info["interp"])
                else:
                    r.fail("binary targets desktop Linux (dynamic loader %s does not exist on Android); "
                           "use a static build or an Android (bionic) build" % info["interp"])
        return r


class ToolCompatibilityChecker:
    """Does this manifest + install method fit this device?"""

    def __init__(self, arch=None, plat=None, sdk=None):
        self._arch, self._plat, self._sdk = arch, plat, sdk

    @property
    def arch(self):
        return self._arch or detect_arch()

    @property
    def platform(self):
        return self._plat or current_platform()

    @property
    def sdk(self):
        return self._sdk if self._sdk is not None else android_sdk()

    def check(self, manifest, method=None):
        r = CheckResult(info={"arch": self.arch, "platform": self.platform, "sdk": self.sdk})
        if self.platform not in manifest.platform:
            r.fail("%s supports %s, this device is %s" % (manifest.name, "/".join(manifest.platform), self.platform))
        abis = [self.arch] + [a for a in supported_abis() if a != self.arch]
        if not set(abis) & set(manifest.architectures):
            r.fail("%s supports %s, this device is %s" % (manifest.name, "/".join(manifest.architectures), self.arch))
        if manifest.min_android_api and self.platform == "android" and self.sdk and self.sdk < manifest.min_android_api:
            r.fail("%s needs Android API %d (device: %d)" % (manifest.name, manifest.min_android_api, self.sdk))
        if method == "download":
            if not any(manifest.sources_for(a) for a in abis):
                r.fail("no download is published for %s" % "/".join(abis))
        return r

    def pick_source(self, manifest):
        for a in [self.arch] + [x for x in supported_abis() if x != self.arch]:
            s = manifest.sources_for(a)
            if s:
                return a, s
        return None, None


def parse_version(text, regex=r"(\d+(?:\.\d+)+)"):
    m = re.search(regex, text or "")
    return m.group(1) if m and m.groups() else (m.group(0) if m else None)


def run_version(path, args, timeout=10, env=None):
    """Run `path args` and return (ok, output). ok = exit code 0."""
    try:
        p = subprocess.run([path] + list(args), capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except PermissionError as e:
        return False, "permission denied (%s)" % e
    except OSError as e:
        return False, "cannot execute: %s" % e
    except subprocess.TimeoutExpired:
        return False, "timed out after %ss" % timeout
