from kivy.metrics import dp
from kivymd.uix.selectioncontrol import MDSwitch

from ...core.errors import NDXError, ValidationError
from ..theme import T
from ..widgets import (ResponsiveGrid, card, chip, column, confirm_dialog, field, form_dialog, icon, label,
                       message_dialog, primary_btn, row, text_btn, toast)
from .base import NDXScreen


class PackagesScreen(NDXScreen):
    key = "packages"
    title = "Packages"

    def actions(self):
        return [["stethoscope", lambda *_: self.doctor()]]

    @property
    def pm(self):
        return self.core.services.get("packages")

    def render(self, body):
        # ---- install box
        c = card()
        c.add_widget(label("Install a plugin", "Subtitle1"))
        c.add_widget(label("GitHub repository URL, a .zip package or a folder path. Nothing is executed during "
                           "installation; you confirm every install.", "Caption", "muted"))
        self.src = field("https://github.com/author/RustSupport  or  /path/pkg.zip")
        self.ver = field("Version (optional)")
        c.add_widget(self.src)
        c.add_widget(self.ver)
        self.status_lbl = label("", "Caption", "muted")
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(primary_btn("Install", self.install))
        r.add_widget(text_btn("Search GitHub", self.search))
        c.add_widget(r)
        c.add_widget(self.status_lbl)
        body.add_widget(c)

        # ---- installed
        installed = self.pm.list_installed()
        body.add_widget(label("Installed plugins (%d)" % len(installed), "Subtitle1"))
        if not installed:
            body.add_widget(label("None yet. Example plugins ship in the app package under plugins/examples.", "Body2", "muted"))
        grid = ResponsiveGrid(min_item_dp=320)
        body.add_widget(grid)
        loader = self.core.services.get("plugins")
        perms = self.core.services.get("permissions")
        for pid, e in sorted(installed.items()):
            cd = card()
            head = row(size_hint_y=None, height=dp(34))
            head.add_widget(icon("puzzle-outline", "accent", 22))
            head.add_widget(label("%s  [color=%s]%s[/color]" % (e.get("name", pid), T.hex("muted"), e["version"]),
                                  "Subtitle2", adaptive_height=False, valign="middle"))
            sw = MDSwitch(active=bool(e.get("enabled", True)), size_hint=(None, None), size=(dp(48), dp(28)))
            sw.bind(active=lambda i, v, pid=pid: self.toggle(pid, v))
            head.add_widget(sw)
            cd.add_widget(head)
            flags = row(size_hint_y=None, height=dp(26), spacing=dp(6))
            flags.add_widget(chip(e.get("category", "?")))
            flags.add_widget(chip("trust: " + perms.trust(pid)))
            flags.add_widget(chip("has code" if e.get("has_code") else "declarative", "warn" if e.get("has_code") else "ok"))
            cd.add_widget(flags)
            if pid in loader.errors:
                cd.add_widget(label(loader.errors[pid], "Caption", "warn"))
            cd.add_widget(label("Source: %s" % e.get("source", "?"), "Caption", "muted"))
            actions = row(size_hint_y=None, height=dp(40))
            if e.get("has_code") and not e.get("code_approved"):
                actions.add_widget(text_btn("Review & approve code", lambda pid=pid: self.approve(pid), "warn"))
            actions.add_widget(text_btn("Info", lambda pid=pid: self.info(pid)))
            if str(e.get("source", "")).startswith("github:"):
                actions.add_widget(text_btn("Update", lambda pid=pid: self.update(pid)))
            if e.get("history"):
                actions.add_widget(text_btn("Rollback", lambda pid=pid: self.rollback(pid), "muted"))
            actions.add_widget(text_btn("Uninstall", lambda pid=pid: self.uninstall(pid), "error"))
            cd.add_widget(actions)
            grid.add_widget(cd)

        # ---- built-in language support (read-only view)
        body.add_widget(label("Language support", "Subtitle1"))
        langs = self.core.services.get("languages")
        lc = card()
        for l in langs.all():
            lc.add_widget(label("%s  [color=%s]%s  ·  %s[/color]" % (
                l.name, T.hex("muted"), " ".join(l.extensions[:3]), l.source), "Body2"))
        body.add_widget(lc)

    # ---- actions
    def _busy(self, text):
        self.status_lbl.text = text

    def install(self):
        src, ver = self.src.text.strip(), self.ver.text.strip() or None
        if not src:
            return
        self._busy("Installing...")

        def work():
            if src.startswith("https://github.com/"):
                return self.pm.install_github(src, ver)
            return self.pm.install_path(src, source="local")

        self.core.tasks.submit(work, name="install " + src, on_done=self._installed, on_error=self._failed)

    def _installed(self, m):
        self._busy("")
        toast("Installed %s %s" % (m.id, m.version))
        self.core.services.get("plugins").reload()
        self.rebuild()

    def _failed(self, exc):
        self._busy("")
        detail = "\n".join("• " + p for p in getattr(exc, "problems", []))
        message_dialog("Operation failed", "%s%s" % (exc, ("\n\n" + detail) if detail else ""))

    def search(self):
        q = self.src.text.strip()
        if not q:
            return
        self._busy("Searching...")
        self.core.tasks.submit(self.pm.search, q, name="search", on_done=self._search_done, on_error=self._failed)

    def _search_done(self, res):
        self._busy("")
        lines = ["%s %s (installed)" % (r["id"], r["version"]) for r in res["installed"]]
        lines += ["%s  *%d\n   %s" % (r["url"], r["stars"], r["description"]) for r in res["github"]]
        if res["error"]:
            lines.append("\nGitHub search unavailable: " + res["error"])
        message_dialog("Search results", "\n".join(lines) or "No results (tag repositories with topic 'ndx-plugin').")

    def toggle(self, pid, value):
        self.pm.store.set_flag(pid, "enabled", bool(value))
        self.core.services.get("plugins").reload()

    def approve(self, pid):
        e = self.pm.store.get(pid)
        m, _ = self.pm.inspect_dir(self.pm.store.version_dir(pid, e["version"]))
        allowed = self.core.config.get("security.allow_plugin_code", False)
        text = ("This plugin contains Python code that runs INSIDE NDX with your app's access. NDX limits what it "
                "can do through its API, but this is not an OS sandbox. Only approve code you trust.\n\n"
                "Requested permissions: %s\n\n%s" % (", ".join(m.permissions) or "none",
                  "" if allowed else "Also enable 'Allow plugin code' in Settings, or it will stay inactive."))
        confirm_dialog("Approve plugin code?", text, lambda: (self.core.services.get("plugins").approve_code(pid, m.permissions),
                                                             self.rebuild()), "Approve", danger=True)

    def info(self, pid):
        e = self.pm.store.get(pid)
        m, problems = self.pm.inspect_dir(self.pm.store.version_dir(pid, e["version"]))
        message_dialog(m.name, "%s\n\nAuthor: %s\nVersion: %s\nCategory: %s\nPermissions: %s\nIntegrity: %s\nSource: %s" % (
            m.description, m.author, m.version, m.category, ", ".join(m.permissions) or "none",
            "OK" if self.pm.verify(pid) else "MODIFIED", e.get("source")))

    def update(self, pid):
        self._busy("Updating...")
        self.core.tasks.submit(self.pm.update, pid, name="update " + pid, on_done=self._installed, on_error=self._failed)

    def rollback(self, pid):
        try:
            v = self.pm.rollback(pid)
            self.core.services.get("plugins").reload()
            toast("Rolled back to " + v)
            self.rebuild()
        except NDXError as e:
            message_dialog("Rollback failed", str(e))

    def uninstall(self, pid):
        def go():
            try:
                self.core.services.get("plugins").unload(pid)
                self.pm.uninstall(pid)
                self.rebuild()
            except NDXError as e:
                message_dialog("Cannot uninstall", str(e))
        confirm_dialog("Uninstall plugin?", "Remove %s and its files?" % pid, go, "Uninstall", danger=True)

    def doctor(self):
        out = []
        self.core.services.get("console").execute("ndx doctor", out.append)
        message_dialog("ndx doctor", "".join(out))
