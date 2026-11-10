"""NDX Kivy/KivyMD application shell: responsive navigation (rail on wide
screens, bottom bar + 'More' on phones), consent dialogs, theming."""
import os
from pathlib import Path

from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.screenmanager import FadeTransition, ScreenManager
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.dialog import MDDialog
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.toolbar import MDTopAppBar

from .. import __version__
from ..core import NDXCore
from ..linux_manager import ProotTarget
from .screens import ALL, EditorScreen, TerminalScreen
from .theme import NAV, T
from .widgets import NavItem, bottom_sheet, text_btn

ROOT = Path(__file__).resolve().parents[2]
PRIMARY = ["home", "projects", "editor", "terminal"]
WIDE_DP = 720


class NDXApp(MDApp):
    title = "NDX"

    def build(self):
        try:                                   # CA bundle for urllib on Android
            import certifi
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        except Exception:
            pass
        assets = ROOT / "assets"
        LabelBase.register(name="NDXMono", fn_regular=str(assets / "fonts" / "DejaVuSansMono.ttf"),
                           fn_bold=str(assets / "fonts" / "DejaVuSansMono-Bold.ttf"))
        self.icon = str(assets / "icon" / "icon_legacy.png")
        Window.softinput_mode = "below_target"

        self.core = NDXCore(main_dispatcher=lambda fn: Clock.schedule_once(lambda dt: fn(), 0), assets_dir=assets)
        self.core.bus.subscribe("ui.consent.request", self._on_consent, on_main=True)
        self.core.start()
        cfg = self.core.config
        T.configure(cfg.get("ui.theme", "dark") == "dark", cfg.get("ui.accent", "Teal"))
        self._apply_material()
        self.run_target = "host"
        self.screens = {}
        self.root_box = MDBoxLayout(orientation="vertical")
        self._compose()
        self.refresh_run_targets()
        Window.bind(width=lambda *_: self._layout(), on_keyboard=self._on_key)
        return self.root_box

    # ---- theming
    def _apply_material(self):
        self.theme_cls.material_style = "M3"
        self.theme_cls.theme_style = "Dark" if T.dark else "Light"
        self.theme_cls.primary_palette = T.accent_name
        Window.clearcolor = T.c("bg")

    def set_theme(self, dark=None, accent=None):
        T.configure(T.dark if dark is None else dark, accent or T.accent_name)
        self.core.config.set("ui.theme", "dark" if T.dark else "light")
        self.core.config.set("ui.accent", T.accent_name)
        self._apply_material()
        current = self.sm.current
        self.root_box.clear_widgets()
        self._compose(reuse=True)
        self.go(current)
        for p in self.screens["terminal"].panes:
            p.apply_theme()

    # ---- composition
    def _compose(self, reuse=False):
        old = dict(self.screens)
        self.sm = ScreenManager(transition=FadeTransition(duration=0.12))
        for cls in ALL:
            if reuse and cls in (EditorScreen, TerminalScreen) and cls.key in old:
                scr = old[cls.key]
                if scr.parent:
                    scr.parent.remove_widget(scr)
            else:
                scr = cls(self)
            self.screens[cls.key] = scr
            self.sm.add_widget(scr)
        self.topbar = MDTopAppBar(title="NDX", elevation=0, md_bg_color=T.c("surface"),
                                  specific_text_color=T.c("text"),
                                  left_action_items=[["console-line", lambda *_: self.go("home")]])
        self.root_box.add_widget(self.topbar)
        self.body = MDBoxLayout(orientation="horizontal")
        self.rail_items, self.bar_items = {}, {}
        self.rail = MDBoxLayout(orientation="vertical", size_hint_x=None, width=dp(84), md_bg_color=T.c("surface"),
                                padding=(dp(4), dp(8)), spacing=dp(2))
        sc = MDScrollView(do_scroll_x=False)
        inner = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(2))
        for key, ic, text in NAV:
            it = NavItem(key, ic, text, self.go)
            self.rail_items[key] = it
            inner.add_widget(it)
        sc.add_widget(inner)
        self.rail.add_widget(sc)
        self.body.add_widget(self.rail)
        self.body.add_widget(self.sm)
        self.root_box.add_widget(self.body)
        self.bar = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(64), md_bg_color=T.c("surface"),
                               padding=(dp(4), 0))
        titles = {k: (ic, t) for k, ic, t in NAV}
        for key in PRIMARY:
            it = NavItem(key, titles[key][0], titles[key][1], self.go)
            self.bar_items[key] = it
            self.bar.add_widget(it)
        more = NavItem("more", "dots-horizontal", "More", lambda k: self.more_menu())
        self.bar_items["more"] = more
        self.bar.add_widget(more)
        self.root_box.add_widget(self.bar)
        self._layout()

    def _layout(self, *_):
        wide = Window.width >= dp(WIDE_DP)
        for w, hide in ((self.rail, not wide), (self.bar, wide)):
            w.opacity = 0 if hide else 1
            w.disabled = hide
        self.rail.width = dp(84) if wide else 0
        self.bar.height = 0 if wide else dp(64)

    def more_menu(self):
        rest = [(ic, t, lambda k=k: self.go(k)) for k, ic, t in NAV if k not in PRIMARY]
        bottom_sheet("More", rest)

    # ---- navigation
    def go(self, key):
        self.sm.current = key
        for k, it in self.rail_items.items():
            it.set_active(k == key)
        for k, it in self.bar_items.items():
            it.set_active(k == key or (k == "more" and key not in PRIMARY))

    def set_topbar(self, title, actions):
        self.topbar.title = title
        self.topbar.right_action_items = actions or []

    def screen(self, key):
        return self.screens[key]

    def current_project(self):
        return self.screens["editor"].project

    def get_saf(self):
        """Create the Android SAF bridge on demand; desktop remains unaffected."""
        if not hasattr(self, "_saf") or self._saf is None:
            from ..android_storage import AndroidSAF, SAFUnavailable
            self._saf = AndroidSAF()
        return self._saf

    def open_project(self, project):
        self.core.services.get("projects").open(project.path)
        self.screens["editor"].open_project(project)
        self.go("editor")

    def _on_key(self, window, key, *args):
        if key == 27 and self.sm.current != "home":      # Android back button
            self.go("home")
            return True
        return False

    # ---- run targets
    def refresh_run_targets(self):
        runner = self.core.services.get("runner")
        for k in [k for k in runner.targets if k.startswith("proot:")]:
            del runner.targets[k]
        linux = self.core.services.get("linux")
        for e in linux.list():
            runner.register_target(ProotTarget(linux, e["id"]))
        if self.run_target not in runner.targets:
            self.run_target = "host"

    def choose_run_target(self):
        runner = self.core.services.get("runner")
        items = [("play-circle-outline", t.title + ("" if t.available() else " (proot missing)"),
                  lambda tid=tid: setattr(self, "run_target", tid)) for tid, t in runner.targets.items()]
        bottom_sheet("Run on", items)

    # ---- consent
    def _on_consent(self, ev):
        p, state = ev.payload, {"done": False}

        def answer(value):
            if not state["done"]:
                state["done"] = True
                p["respond"](value)

        text = p["message"] + ("\n\n" + "\n".join("• " + d for d in p["details"]) if p["details"] else "")
        dlg = MDDialog(title=p["title"], text=text, buttons=[
            text_btn("Deny", lambda: (answer(False), dlg.dismiss()), "muted"),
            text_btn("Allow", lambda: (answer(True), dlg.dismiss()))],
            on_dismiss=lambda *_: answer(False))
        dlg.open()

    # ---- lifecycle
    def on_start(self):
        last = self.core.config.get("session.last_project", "")
        pm = self.core.services.get("projects")
        if last and os.path.isdir(last):
            try:
                self.screens["editor"].project = pm.open(last)
            except Exception:
                pass
        self.go("home")

    def on_pause(self):
        return True

    def on_stop(self):
        saf = getattr(self, "_saf", None)
        if saf is not None:
            try:
                saf.close()
            except Exception:
                pass
        try:
            self.screens["editor"]._sync_text()
            self.screens["editor"].session.persist()
        finally:
            self.core.stop()
