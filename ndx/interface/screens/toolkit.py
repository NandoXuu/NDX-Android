import platform

from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView

from ...android_toolkit import device_info, explorer, open_developer_settings
from ...core.errors import NDXError
from ..theme import T
from ..widgets import (ResponsiveGrid, card, chip, column, field, icon, label, message_dialog, primary_btn, row,
                       text_btn, toast)
from .base import NDXScreen


class ToolkitScreen(NDXScreen):
    key = "toolkit"
    title = "Device Toolkit"
    refresh_interval = 2

    @property
    def adb(self):
        return self.core.services.get("adb")

    @property
    def logcat(self):
        return self.core.services.get("logcat")

    def render(self, body):
        # ---- profile
        c = card()
        c.add_widget(label("Device profile", "Subtitle1"))
        info = device_info()
        for k, v in info.items():
            c.add_widget(label("%s: %s" % (k, v), "Caption", "muted"))
        mon = self.core.services.get("monitor")
        snap = mon.snapshot()
        for name in ("memory", "storage"):
            for k, m in snap[name].items():
                if m["value"] is not None:
                    c.add_widget(label("%s.%s: %s  (%s)" % (name, k, m["value"], m["quality"]), "Caption", "muted"))
        c.add_widget(text_btn("Open Developer options", self._dev_settings))
        body.add_widget(c)

        # ---- ADB
        c = card()
        c.add_widget(label("ADB & wireless debugging", "Subtitle1"))
        st = self.adb.status()
        tone = {"connected": "ok", "adb_unavailable": "warn", "authorization_required": "warn"}.get(st["state"], "muted")
        c.add_widget(label("ADB state: %s" % st["state"].replace("_", " "), "Body2", tone))
        c.add_widget(label(st["message"], "Caption", "muted"))
        if st["state"] == "adb_unavailable":
            c.add_widget(text_btn("Install adb", self.install_adb))
        c.add_widget(label("Pairing uses Android's own flow: Developer options > Wireless debugging > Pair device "
                           "with pairing code. NDX only runs `adb pair/connect` with what you enter.", "Caption", "muted"))
        self.pair_hp, self.pair_code, self.conn_hp = field("Pairing host:port"), field("Pairing code"), field("Connect host:port")
        for w in (self.pair_hp, self.pair_code, self.conn_hp):
            c.add_widget(w)
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(primary_btn("Pair", self.pair))
        r.add_widget(text_btn("Connect", self.connect))
        r.add_widget(text_btn("Devices", self.devices))
        c.add_widget(r)
        body.add_widget(c)

        # ---- logcat
        c = card()
        c.add_widget(label("Logcat monitor", "Subtitle1"))
        self.filter = field("Filter text")
        c.add_widget(self.filter)
        r = row(size_hint_y=None, height=dp(44))
        running = self.logcat.proc is not None
        r.add_widget(primary_btn("Stop" if running else "Start", self.toggle_logcat))
        r.add_widget(text_btn("Crash logs", self.show_crashes))
        r.add_widget(text_btn("NDX log", self.show_own_log))
        c.add_widget(r)
        sc = ScrollView(size_hint_y=None, height=dp(180))
        self.log_lbl = label("", "Caption", font_name="NDXMono", markup=False, padding=(dp(6), dp(4)))
        sc.add_widget(self.log_lbl)
        c.add_widget(sc)
        body.add_widget(c)
        self.refresh()

        # ---- capabilities
        body.add_widget(label("Developer Capabilities Explorer", "H6"))
        rows, probes = explorer()
        body.add_widget(label("Classification of what an ordinary Android app can do. Nothing here bypasses "
                              "Android security.", "Caption", "muted"))
        for grp in ("Read", "Navigate", "Control"):
            gc = card()
            gc.add_widget(label(grp, "Subtitle1"))
            for r_ in [x for x in rows if x["group"] == grp]:
                rr = column(spacing=dp(2), size_hint_y=None)
                rr.add_widget(label("%s   [color=%s]%s[/color]" % (r_["name"], T.hex(T.status_color(r_["status"])), r_["status"]), "Body2"))
                rr.add_widget(label(r_["note"], "Caption", "muted"))
                gc.add_widget(rr)
            body.add_widget(gc)

    def refresh(self):
        if hasattr(self, "log_lbl"):
            txt = getattr(self, "filter", None)
            lines = self.logcat.filter(txt.text if txt else "")[-80:]
            self.log_lbl.text = "\n".join(lines) if lines else "Logcat is stopped or empty."

    # ---- actions
    def _dev_settings(self):
        ok, msg = open_developer_settings()
        toast(msg)

    def install_adb(self):
        tc = self.core.services.get("toolchain")
        self.core.tasks.submit(lambda: tc.install("adb"), name="install adb",
                               on_done=lambda r: (message_dialog("adb", "Installation finished: check the ADB state above."),
                                                  self.rebuild()),
                               on_error=lambda e: message_dialog("adb not installed", str(e)))

    def _adb(self, fn, *a):
        def work():
            return fn(*a)
        self.core.tasks.submit(work, name="adb", on_done=lambda r: message_dialog("ADB", str(r[1] if isinstance(r, tuple) else r)),
                               on_error=lambda e: message_dialog("ADB unavailable", str(e)))

    def pair(self):
        self._adb(self.adb.pair, self.pair_hp.text.strip(), self.pair_code.text.strip())

    def connect(self):
        self._adb(self.adb.connect, self.conn_hp.text.strip())

    def devices(self):
        def fmt():
            d = self.adb.devices()
            return (True, "\n".join("%s  %s  %s" % (x["serial"], x["state"], x["info"]) for x in d) or "No devices")
        self._adb(fmt)

    def toggle_logcat(self):
        try:
            if self.logcat.proc:
                self.logcat.stop()
            else:
                self.logcat.start()
        except NDXError as e:
            message_dialog("Logcat unavailable", str(e))
        self.rebuild()

    def show_crashes(self):
        files = self.core.services.get("crashlogs").list()
        message_dialog("Crash logs", "\n".join(f.name for f in files[:20]) or "No crash logs captured.")

    def show_own_log(self):
        message_dialog("NDX log", "\n".join(m for _t, _l, _n, m in self.core.logs.recent(40)) or "(empty)")
