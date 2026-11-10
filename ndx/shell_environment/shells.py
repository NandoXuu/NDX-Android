"""Detects installed shells/utilities and builds shell profiles.
Android shell, Termux and PRoot environments are kept as separate kinds."""
import os
from dataclasses import dataclass, field

from ..core.registry import Module
from ..toolchain_manager.manager import host_which as which

SHELLS = ["bash", "zsh", "fish", "sh", "ash"]
UTILITIES = ["git", "curl", "wget", "ssh", "make", "cmake", "gcc", "clang", "grep", "find", "tree", "tar",
             "zip", "unzip", "less", "vim", "nano", "top", "htop", "busybox", "ranger", "yazi"]


@dataclass
class ShellProfile:
    id: str
    title: str
    argv: list
    kind: str = "android"          # android | proot | external
    env: dict = field(default_factory=dict)
    cwd: str = ""
    note: str = ""


def detect_shells(extra_dirs=()):
    found = {}
    for s in SHELLS:
        p = which(s, extra_dirs)
        if p:
            found[s] = p
    if os.path.exists("/system/bin/sh"):
        found.setdefault("sh", "/system/bin/sh")
    if "busybox" not in found:
        b = which("busybox", extra_dirs)
        if b:
            found["busybox"] = b
    return found


def detect_utilities(extra_dirs=()):
    return {u: which(u, extra_dirs) for u in UTILITIES}


class ShellEnvironment(Module):
    name = "shell"
    title = "Shell Environment"

    def __init__(self, paths, config):
        self.paths, self.config = paths, config
        self.shells, self.utils = {}, {}
        self.user_profiles = []
        self.env_vars = {"LANG": "C.UTF-8"}
        self.env_provider = None      # fn() -> environment of the host runtime (managed PATH, tool variables)

    def start(self, core):
        self.rescan()

    def rescan(self):
        """Re-detect shells/utilities (called at start and whenever the toolchain changes)."""
        extra = [self.paths.bin]
        self.shells = detect_shells(extra)
        self.utils = detect_utilities(extra)
        self.status = "running" if self.shells else "unavailable"
        self.error = "" if self.shells else "No shell binary found on this device. Install one: `ndx tool install bash`"

    def profiles(self, cwd=None):
        out = []
        for name, path in self.shells.items():
            argv = [path, "-i"] if name != "busybox" else [path, "sh"]
            if name == "fish":
                argv = [path, "-i"]
            env = dict(self.env_provider() or {}) if self.env_provider else {}
            env.update(self.env_vars)
            out.append(ShellProfile("host-" + name, name + " (Android host)", argv, "android",
                                    env, cwd or str(self.paths.base)))
        return out + self.user_profiles

    def default_profile(self, cwd=None):
        wanted = self.config.get("terminal.default_shell", "")
        profs = self.profiles(cwd)
        for p in profs:
            if p.id == wanted:
                return p
        for pref in ("bash", "zsh", "sh", "ash", "busybox"):
            for p in profs:
                if p.id == "host-" + pref:
                    return p
        return profs[0] if profs else None

    @staticmethod
    def oh_my_zsh_plan():
        """Plan only. NDX never runs the installer without consent, and only inside
        a Linux environment that already has zsh, git and curl."""
        return {"requires": ["zsh", "git", "curl"],
                "command": 'sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended',
                "network": True}
