"""Shared fixtures: an offline Toolchain Manager with a mock downloader and mock executables."""
import hashlib
import io
import os
import struct
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ndx.core.config import ConfigManager          # noqa: E402
from ndx.core.consent import ConsentGateway        # noqa: E402
from ndx.core.events import EventBus               # noqa: E402
from ndx.core.paths import Paths                   # noqa: E402
from ndx.resource_manager import ResourceManager   # noqa: E402
from ndx.toolchain_manager import ToolchainManager, ToolDownloader   # noqa: E402

HOST = "downloads.example.test"
ARCH_ANY = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]


def script_tool(name="mytool", version="1.2.3", exit_code=0):
    return ("#!/bin/sh\necho '%s %s'\nexit %d\n" % (name, version, exit_code)).encode()


def make_targz(members):
    """members: {path: bytes|(bytes, mode)} -> tar.gz bytes"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, val in members.items():
            data, mode = val if isinstance(val, tuple) else (val, 0o755)
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), mode
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def manifest(tid="mytool", version="1.0.0", url=None, data=None, pin=True, deps=None, extra=None):
    url = url or "https://%s/%s-%s.tar.gz" % (HOST, tid, version)
    src = {"url": url, "archive": "tar.gz", "size": len(data or b""), "installed_size": 4096}
    if pin and data is not None:
        src["sha256"] = sha(data)
    m = {"id": tid, "name": tid.title(), "version": version, "executable": "bin/" + tid, "exe_format": "script",
         "platform": ["android", "linux"], "architectures": ARCH_ANY, "allowed_hosts": [HOST],
         "version_args": ["--version"], "version_regex": r"(\d+\.\d+\.\d+)",
         "install": [{"method": "download", "sources": {a: dict(src) for a in ARCH_ANY}}]}
    if deps:
        m["requires"] = deps
    m.update(extra or {})
    return m


class FakeWeb:
    """Opener for ToolDownloader: serves in-memory bytes keyed by URL, records every request."""

    def __init__(self):
        self.files, self.requests = {}, []

    def __call__(self, url, timeout, allowed_hosts):
        self.requests.append(url)
        if url not in self.files:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return io.BytesIO(self.files[url])


class Env:
    """A temp NDX home with a ToolchainManager wired to FakeWeb."""

    def __init__(self, consent_answer=True, probe=(True, "ok"), with_resources=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = Paths(self.tmp.name).ensure()
        self.bus = EventBus()
        self.config = ConfigManager(self.paths.data / "config.json", self.bus)
        self.consent = ConsentGateway(self.bus)
        self.answer = consent_answer
        self.asked = []
        self.bus.subscribe("ui.consent.request", self._ask)
        self.web = FakeWeb()
        self.resources = ResourceManager(1024, self.bus, None, self.paths.base) if with_resources else None
        self.tc = ToolchainManager(self.paths, self.bus, self.config, None, self.consent, None, self.resources,
                                   downloader=ToolDownloader(self.web), native_lib_dir=lambda: None,
                                   probe=lambda: probe)
        self.tc.checker._arch, self.tc.checker._plat = "x86_64", "linux"

    def _ask(self, ev):
        self.asked.append(ev.payload["title"])
        ans = self.answer(ev.payload) if callable(self.answer) else self.answer
        ev.payload["respond"](ans)

    def publish(self, tid="mytool", version="1.0.0", exe=None, pin=True, deps=None, extra=None):
        data = make_targz({"bin/" + tid: exe if exe is not None else script_tool(tid, version)})
        m = manifest(tid, version, data=data, pin=pin, deps=deps, extra=extra)
        self.web.files[m["install"][0]["sources"]["x86_64"]["url"]] = data
        self.tc.registry.add_user_manifest(m)
        return m, data

    def close(self):
        self.tc.stop()
        self.tmp.cleanup()


def fake_elf(path, machine=0xB7, interp=None, bits=64):
    """Write a minimal ELF header (+ PT_INTERP when given) for validator tests."""
    ph = b""
    body = b""
    phnum = 0
    if interp:
        data = interp.encode() + b"\0"
        off = 64 + 56
        ph = struct.pack("<IIQQQQQQ", 3, 4, off, 0, 0, len(data), len(data), 1)
        body, phnum = data, 1
    hdr = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    hdr += struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, 64, 0, 0, 64, 56, phnum, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(hdr + ph + body)
    os.chmod(path, 0o755)
    return path
