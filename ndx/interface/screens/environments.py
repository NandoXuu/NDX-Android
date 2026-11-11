import os
from pathlib import Path

from kivy.metrics import dp

from ...core.errors import NDXError
from ...gui_compatibility import TOOLKITS, LinuxGuiBridge
from ...linux_manager import DISTROS, LIMITATIONS
from ...utilities import human_size
from ..theme import T
from ..widgets import (ResponsiveGrid, card, chip, column, confirm_dialog, field, form_dialog, icon, label,
                       message_dialog, primary_btn, row, text_btn, toast, divider)
from .base import NDXScreen

LANGUAGE_TOOLS = {"python": "python3", "lua": "lua", "c": "gcc", "cpp": "gcc", "rust": "rustc", "java": "jdk",
                  "javascript": "node", "typescript": "node", "shell": "bash"}


class EnvironmentsScreen(NDXScreen):
    key = "environments"
    title = "Environments"

    @property
    def lm(self):
        return self.core.services.get("linux")

    @property
    def tc(self):
        return self.core.services.get("toolchain")

    def render(self, body):
        sv = self.core.services
        body.add_widget(label("Linux environments (PRoot)", "H6"))
        c = card()
        avail = self.lm.backend.available()
        c.add_widget(label("PRoot: " + ("installed and runnable" if avail else
                                        "not installed. NDX can install it for you (verified, with rollback)."),
                           "Body2", "ok" if avail else "warn"))
        c.add_widget(label("PRoot is not a virtual machine.", "Caption", "muted"))
        br = row(size_hint_y=None, height=dp(40))
        if not avail:
            br.add_widget(primary_btn("Install PRoot", self.install_proot))
        br.add_widget(text_btn("Check Linux Environment", self.check_linux))
        br.add_widget(text_btn("Limitations", lambda: message_dialog("PRoot limitations", "\n\n".join("• " + l for l in LIMITATIONS))))
        c.add_widget(br)
        body.add_widget(c)
        envs = self.lm.list()
        grid = ResponsiveGrid(min_item_dp=320)
        body.add_widget(grid)
        for e in envs:
            cd = card()
            head = row(size_hint_y=None, height=dp(34))
            head.add_widget(icon("layers-outline", "accent", 22))
            head.add_widget(label("%s  [color=%s](%s)[/color]" % (e["name"], T.hex("muted"), e["id"]), "Subtitle2",
                                  adaptive_height=False, valign="middle"))
            cd.add_widget(head)
            cd.add_widget(label("Rootfs: %s   Package manager: %s" % (human_size(self.lm.size(e["id"])), e["pkg"]), "Caption", "muted"))
            a = row(size_hint_y=None, height=dp(40))
            a.add_widget(primary_btn("Open shell", lambda e=e: self.open_shell(e)))
            a.add_widget(text_btn("Install pkg", lambda e=e: self.pkg_dialog(e)))
            a.add_widget(text_btn("Backup", lambda e=e: self.backup(e)))
            a.add_widget(text_btn("Remove", lambda e=e: self.remove(e), "error"))
            cd.add_widget(a)
            grid.add_widget(cd)
        if not envs:
            body.add_widget(label("No environments installed.", "Body2", "muted"))
        ic = card()
        ic.add_widget(label("Install a distribution", "Subtitle1"))
        for did, d in DISTROS.items():
            r = row(size_hint_y=None, height=dp(40))
            r.add_widget(label(d["name"], "Body2", adaptive_height=False, valign="middle"))
            if d["url"]:
                r.add_widget(text_btn("Download", lambda did=did: self.install(did)))
            else:
                r.add_widget(text_btn("From URL", lambda did=did: self.install_url(did)))
            r.add_widget(text_btn("From file", lambda did=did: self.install_file(did), "muted"))
            ic.add_widget(r)
        ic.add_widget(label("Downloads ask for confirmation and need storage. Distributions without a built-in link "
                            "(Debian) take an https rootfs URL or a local tarball.", "Caption", "muted"))
        body.add_widget(ic)

        body.add_widget(label("Tool installer", "H6"))
        ic = card()
        ic.add_widget(label("One tap installs a tool or a whole profile. Tools that run in Linux use the first "
                            "environment you installed.", "Caption", "muted"))
        self.tool_search = field("Search tools, languages or capabilities")
        ic.add_widget(self.tool_search)
        self.tool_box = column()
        ic.add_widget(self.tool_box)
        self.tool_search.bind(text=lambda _inst, txt: self._fill_tools(txt))
        body.add_widget(ic)
        self._fill_tools(self.tool_search.text)

        body.add_widget(label("Host shell environment", "H6"))
        sh = sv.get("shell")
        c = card()
        c.add_widget(label("Shells: " + (", ".join(sh.shells) or "none found"), "Body2"))
        found = [u for u, p in sh.utils.items() if p]
        c.add_widget(label("Utilities (%d of %d): %s" % (len(found), len(sh.utils), ", ".join(found) or "none found"),
                           "Body2"))
        c.add_widget(label("Missing: " + ", ".join(u for u, p in sh.utils.items() if not p), "Caption", "muted"))
        c.add_widget(label("The Android host has only a minimal shell. Install a Linux environment above for git, gcc, "
                           "zsh, vim and more. Termux is a separate app: NDX can only run commands there if you enable the optional integration in Settings.", "Caption", "muted"))
        body.add_widget(c)

        body.add_widget(label("Languages & toolchains", "H6"))
        langs = sv.get("languages")
        grid2 = ResponsiveGrid(min_item_dp=280)
        body.add_widget(grid2)
        for l in langs.all():
            cd = card(padding=dp(12))
            st = langs.toolchain_status(l)
            ok = bool(st) and all(st.values())
            cd.add_widget(label("%s   [color=%s]%s[/color]" % (l.name, T.hex("ok" if ok or not st else "warn"),
                                                              "ready" if ok else ("no toolchain needed" if not st else "missing")),
                                "Subtitle2"))
            for t, p in st.items():
                cd.add_widget(label("%s: %s" % (t, p or "not found"), "Caption", "muted"))
            if l.environment:
                cd.add_widget(label(l.environment, "Caption", "muted"))
            tool = LANGUAGE_TOOLS.get(l.id)
            if st and not ok and tool:
                cd.add_widget(text_btn("Install " + tool, lambda tid=tool: self.install_tool(tid)))
            grid2.add_widget(cd)
        body.add_widget(label("\"ready\" means available on the Android host. Tools installed by the installer run "
                              "inside a Linux environment: use a PRoot target to build and run them.", "Caption", "muted"))

        body.add_widget(label("GUI toolkit compatibility", "H6"))
        gb = LinuxGuiBridge(self.core.paths.bin).status()
        c = card()
        c.add_widget(label(gb["detail"], "Caption", "warn"))
        body.add_widget(c)
        grid3 = ResponsiveGrid(min_item_dp=280)
        body.add_widget(grid3)
        for t in TOOLKITS:
            cd = card(padding=dp(12))
            cd.add_widget(label("%s  [color=%s]%s[/color]" % (t["name"], T.hex("warn"), t["status"]), "Subtitle2"))
            cd.add_widget(label("%s · ARM64: %s" % (t["approach"], t["arm64"]), "Caption", "muted"))
            cd.add_widget(label("Requires: " + t["requires"], "Caption", "muted"))
            if t["note"]:
                cd.add_widget(label(t["note"], "Caption", "muted"))
            grid3.add_widget(cd)

    def _first_env(self):
        envs = self.lm.list()
        return envs[0]["id"] if envs else None

    def _fill_tools(self, query):
        self.tool_box.clear_widgets()
        tc = self.tc
        if tc is None:
            self.tool_box.add_widget(label("The Toolchain Manager is not available.", "Body2", "warn"))
            return
        q = (query or "").strip().lower()
        if not q:
            for name, p in tc.profiles().items():
                pr = row(size_hint_y=None, height=dp(40))
                pr.add_widget(label("Profile: " + p["title"], "Body2", adaptive_height=False, valign="middle"))
                pr.add_widget(text_btn("Install profile", lambda n=name: self.install_profile(n)))
                self.tool_box.add_widget(pr)
            self.tool_box.add_widget(divider())
        shown = 0
        for r in tc.list():
            hay = " ".join([r["id"], r["name"]] + list(r["capabilities"] or [])).lower()
            if q and q not in hay:
                continue
            self.tool_box.add_widget(self._tool_row(r))
            shown += 1
        if not shown:
            self.tool_box.add_widget(label("No tool matches \"%s\"." % (query or "").strip(), "Caption", "muted"))

    def _tool_row(self, r):
        installed = bool(r["installed"])
        cd = row(size_hint_y=None, height=dp(44), spacing=dp(6))
        cd.add_widget(label("%s  [color=%s](%s)[/color]" % (r["name"], T.hex("muted"), r["id"]), "Body2",
                            adaptive_height=False, valign="middle"))
        cd.add_widget(chip("installed" if installed else "missing", "ok" if installed else "muted"))
        if installed:
            scope = sorted(r["installed"])[0]
            cd.add_widget(text_btn("Remove", lambda tid=r["id"], s=scope: self.remove_tool(tid, s), "error"))
        else:
            cd.add_widget(primary_btn("Install", lambda tid=r["id"]: self.install_tool(tid)))
        return cd

    def _run_job(self, fn, name, ok_text):
        toast("Working: " + name)
        self.core.tasks.submit(fn, name=name,
                               on_done=lambda _res: (toast(ok_text), self.rebuild()),
                               on_error=lambda ex: message_dialog("Failed: " + name, str(ex)))

    def install_tool(self, tid):
        tc, env = self.tc, self._first_env()
        self._run_job(lambda: tc.install(tid, env=env, confirm=True), "install " + tid, "%s installed" % tid)

    def remove_tool(self, tid, scope):
        tc = self.tc
        confirm_dialog("Remove %s?" % tid, "Remove %s from %s." % (tid, scope),
                       lambda: self._run_job(lambda: tc.remove(tid, scope=scope, confirmed=True), "remove " + tid,
                                             "%s removed" % tid), "Remove", danger=True)

    def install_profile(self, name):
        tc = self.tc
        try:
            plan = tc.plan_profile(name)
        except NDXError as ex:
            message_dialog("Profile", str(ex))
            return
        lines = ["Estimated size: %s" % plan["estimate_text"]]
        lines += ["• %s%s" % (t["id"], " (already installed)" if t["installed"] else "") for t in plan["tools"]]
        if plan["fits"] is False:
            lines.append("Not enough free storage for this profile.")
        env = self._first_env()
        confirm_dialog("Install %s?" % plan["title"], "\n".join(lines),
                       lambda: self.core.tasks.submit(lambda: tc.install_profile(name, env=env, confirm=True),
                                                      name="profile " + name,
                                                      on_done=lambda res: self._profile_done(name, res),
                                                      on_error=lambda ex: message_dialog("Profile failed", str(ex))),
                       "Install")

    def _profile_done(self, name, res):
        failed = res.get("failed") or {}
        text = "Installed %d tool(s)." % len(res.get("installed") or [])
        if failed:
            text += "\n\nFailed:\n" + "\n".join("• %s: %s" % (k, v) for k, v in failed.items())
        message_dialog("Profile " + name, text)
        self.rebuild()

    def open_shell(self, e):
        try:
            prof = self.lm.profile(e["id"])
        except NDXError as ex:
            message_dialog("Cannot start environment", str(ex))
            return
        self.app.go("terminal")
        self.app.screen("terminal").new_shell(prof)

    def install_proot(self):
        tc = self.tc
        if tc is None:
            message_dialog("Toolchain unavailable", "The Toolchain Manager is not available.")
            return
        toast("Installing PRoot...")
        self.core.tasks.submit(lambda: tc.install("proot"), name="install proot",
                               on_done=lambda r: (message_dialog("PRoot", "Installed and verified." if getattr(r, "ok", True)
                                                                 else "Not installed: %s" % getattr(r, "message", r)),
                                                  self.rebuild()),
                               on_error=lambda ex: message_dialog("PRoot not installed", str(ex)))

    def check_linux(self):
        toast("Checking...")

        def fmt(rep):
            lines = ["Overall: %s" % rep["status"].upper()]
            for c in rep["checks"]:
                lines.append("[%s] %s: %s" % (c["status"], c["name"], c["detail"]))
            for e in rep["environments"]:
                lines.append("Environment %s: %s" % (e["id"], e["status"].upper()))
                lines += ["  [%s] %s: %s" % (c["status"], c["name"], c["detail"]) for c in e["checks"]]
            return "\n".join(lines)
        self.core.tasks.submit(lambda: fmt(self.lm.check_environment()), name="check linux",
                               on_done=lambda t: message_dialog("Linux environment check", t),
                               on_error=lambda ex: message_dialog("Check failed", str(ex)))

    def install(self, distro, url=None):
        toast("Starting download...")
        self.core.tasks.submit(lambda: self.lm.install(distro, url=url, auto_install_proot=True), name="install " + distro,
                               on_done=lambda _: (self._register_targets(), self.rebuild()),
                               on_error=lambda ex: message_dialog("Install failed", str(ex)))

    def install_url(self, distro):
        form_dialog("Install from URL", [("url", "https://.../rootfs.tar.gz", "")],
                    lambda v: self.install(distro, url=v["url"].strip() or None), "Install")

    def install_file(self, distro):
        form_dialog("Install from tarball", [("path", "Path to rootfs .tar/.tar.gz", "")],
                    lambda v: self.core.tasks.submit(lambda: self.lm.install(distro, archive=v["path"], auto_install_proot=True),
                                                     name="install " + distro,
                                                     on_done=lambda _: (self._register_targets(), self.rebuild()),
                                                     on_error=lambda ex: message_dialog("Install failed", str(ex))), "Install")

    def _register_targets(self):
        self.app.refresh_run_targets()

    def pkg_dialog(self, e):
        form_dialog("Install package in %s" % e["id"], [("pkg", "Package name", "")],
                    lambda v: self._pkg(e, v["pkg"]), "Open terminal")

    def _pkg(self, e, pkg):
        try:
            argv = self.lm.package_command(e["id"], "install", pkg.strip())
        except Exception as ex:
            message_dialog("Error", str(ex))
            return
        self.open_shell(e)
        self.app.screen("terminal").active_pane.send_text(" ".join(argv) + "\r")

    def backup(self, e):
        dest = str(self.core.paths.cache / ("%s-backup.tar.gz" % e["id"]))
        self.core.tasks.submit(lambda: self.lm.backup(e["id"], dest), name="backup",
                               on_done=lambda p: message_dialog("Backup created", p),
                               on_error=lambda ex: message_dialog("Backup failed", str(ex)))

    def remove(self, e):
        confirm_dialog("Remove environment?", "Delete %s and everything inside it?" % e["id"],
                       lambda: (self.lm.remove(e["id"], confirmed=True), self._register_targets(), self.rebuild()),
                       "Remove", danger=True)
