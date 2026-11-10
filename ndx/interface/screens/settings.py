from kivy.metrics import dp
from kivymd.uix.selectioncontrol import MDSwitch

from ... import __version__
from ...android_toolkit import request_notification_permission, start_keepalive_service, stop_keepalive_service
from ...core.paths import is_android
from ..theme import ACCENTS, T
from ..widgets import (card, column, confirm_dialog, icon, label, message_dialog, primary_btn, row, text_btn, toast,
                       icon_btn)
from .base import NDXScreen


class SettingsScreen(NDXScreen):
    key = "settings"
    title = "Settings"

    def test_termux(self):
        tx = self.core.services.get("termux")
        self.core.tasks.submit(lambda: tx.get_status(probe=True, force=True), name="termux test",
                               on_done=lambda st: (message_dialog("Termux", "%s\n%s" % (st["state"], st["detail"])), self.rebuild()),
                               on_error=lambda e: message_dialog("Termux", str(e)))

    def render(self, body):
        cfg = self.core.config
        # ---- tools & Termux
        c = card()
        c.add_widget(label("Tools & Termux", "Subtitle1"))
        tx = self.core.services.get("termux")
        if tx is not None:
            stt = tx.get_status()
            c.add_widget(label("Termux integration: %s" % stt["state"].replace("_", " "), "Body2"))
            c.add_widget(label(stt["detail"], "Caption", "muted"))
            for g in stt.get("guidance", [])[:3]:
                c.add_widget(label("• " + g, "Caption", "muted"))
            c.add_widget(text_btn("Test Termux connection", self.test_termux))
        c.add_widget(label("Manage tools from the terminal: `ndx tool list`, `ndx tool install <id>`.", "Caption", "muted"))
        body.add_widget(c)

        # ---- appearance
        c = card()
        c.add_widget(label("Appearance", "Subtitle1"))
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(label("Dark theme", "Body1", adaptive_height=False, valign="middle"))
        sw = MDSwitch(active=T.dark, size_hint=(None, None), size=(dp(48), dp(28)), pos_hint={"center_y": .5})
        sw.bind(active=lambda i, v: self.app.set_theme(dark=v))
        r.add_widget(sw)
        c.add_widget(r)
        c.add_widget(label("Accent color", "Body2", "muted"))
        ar = row(size_hint_y=None, height=dp(44))
        for name in ACCENTS:
            ar.add_widget(text_btn(name, lambda n=name: self.app.set_theme(accent=n), "accent" if name == T.accent_name else "muted"))
        c.add_widget(ar)
        body.add_widget(c)

        # ---- editor
        c = card()
        c.add_widget(label("Editor", "Subtitle1"))
        c.add_widget(self._stepper("Font size", "editor.font_size", 14, 8, 28))
        c.add_widget(self._stepper("Tab width", "editor.tab_width", 4, 2, 8))
        c.add_widget(self._switch("Line numbers", "editor.line_numbers", True))
        c.add_widget(self._switch("Autosave on edit", "editor.autosave", False))
        body.add_widget(c)

        # ---- terminal
        c = card()
        c.add_widget(label("Terminal", "Subtitle1"))
        c.add_widget(self._stepper("Font size", "terminal.font_size", 12, 8, 22))
        shells = [p.id for p in self.core.services.get("shell").profiles()]
        c.add_widget(label("Default shell: %s" % (cfg.get("terminal.default_shell") or "auto"), "Body2", "muted"))
        sr = row(size_hint_y=None, height=dp(40))
        for sid in shells[:4]:
            sr.add_widget(text_btn(sid.replace("host-", ""), lambda s=sid: (cfg.set("terminal.default_shell", s), self.rebuild())))
        c.add_widget(sr)
        body.add_widget(c)

        # ---- security
        c = card()
        c.add_widget(label("Security", "Subtitle1"))
        c.add_widget(self._switch("Allow plugin code to run", "security.allow_plugin_code", False,
                                  confirm="Python plugins run inside NDX. Even with this on, each plugin must be "
                                          "individually approved. Enable only if you trust your plugins."))
        c.add_widget(label("Local servers bind to 127.0.0.1 by default. External binding requires an explicit "
                           "per-server opt-in.", "Caption", "muted"))
        body.add_widget(c)

        # ---- android
        c = card()
        c.add_widget(label("Android", "Subtitle1"))
        c.add_widget(label("Notifications are only needed for the foreground service (Android 13+). NDX asks "
                           "for it only here, when you press the button.", "Caption", "muted"))
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(text_btn("Allow notifications", lambda: toast("Requested" if request_notification_permission() else "Not available here")))
        r.add_widget(text_btn("Start keep-alive service", lambda: toast(start_keepalive_service()[1])))
        r.add_widget(text_btn("Stop", lambda: toast("Stopped" if stop_keepalive_service() else "Not running")))
        c.add_widget(r)
        c.add_widget(label("The keep-alive service holds its own process; it does not keep servers started in the "
                           "app alive (see Known limitations).", "Caption", "warn"))
        body.add_widget(c)

        # ---- about
        c = card()
        c.add_widget(label("About", "Subtitle1"))
        c.add_widget(label("NDX %s · Android: %s · data: %s" % (__version__, is_android(), self.core.paths.base), "Caption", "muted"))
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(text_btn("Logs", lambda: message_dialog("Recent log", "\n".join(m for *_, m in self.core.logs.recent(40)))))
        r.add_widget(text_btn("Modules", lambda: message_dialog("Modules", "\n".join(
            "%s: %s %s" % (m.title, m.status, m.error) for m in self.core.modules.all()))))
        c.add_widget(r)
        body.add_widget(c)

    def _switch(self, text, key, default, confirm=None):
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(label(text, "Body1", adaptive_height=False, valign="middle"))
        sw = MDSwitch(active=bool(self.core.config.get(key, default)), size_hint=(None, None), size=(dp(48), dp(28)),
                      pos_hint={"center_y": .5})

        def changed(inst, value):
            if value and confirm:
                inst.active = False
                confirm_dialog("Are you sure?", confirm, lambda: (self.core.config.set(key, True), self.rebuild()), "Enable", danger=True)
            else:
                self.core.config.set(key, bool(value))
        sw.bind(active=changed)
        r.add_widget(sw)
        return r

    def _stepper(self, text, key, default, lo, hi):
        cfg = self.core.config
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(label("%s: %s" % (text, cfg.get(key, default)), "Body1", adaptive_height=False, valign="middle"))
        r.add_widget(icon_btn("minus", lambda: (cfg.set(key, max(lo, cfg.get(key, default) - 1)), self.rebuild())))
        r.add_widget(icon_btn("plus", lambda: (cfg.set(key, min(hi, cfg.get(key, default) + 1)), self.rebuild())))
        return r
