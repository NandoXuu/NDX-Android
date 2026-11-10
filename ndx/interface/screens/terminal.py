import os

from kivy.core.clipboard import Clipboard
from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView
from kivymd.uix.boxlayout import MDBoxLayout

from ...terminal import ConsoleSession, parse_combo, combo_bytes
from ..terminal_widgets import TerminalPane, TermInput
from ..theme import T
from ..widgets import (bottom_sheet, form_dialog, icon_btn, label, message_dialog, row, text_btn, toast)
from .base import NDXScreen

DEFAULT_MACROS = [{"label": "^C", "keys": "ctrl+c"}, {"label": "^D", "keys": "ctrl+d"},
                  {"label": "^Z", "keys": "ctrl+z"}, {"label": "^L", "keys": "ctrl+l"}]


def _path_executables():
    names = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            for f in os.listdir(d):
                if os.access(os.path.join(d, f), os.X_OK):
                    names.add(f)
        except OSError:
            continue
    return sorted(names)


class TerminalScreen(NDXScreen):
    key = "terminal"
    title = "Terminal"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        self.page.clear_widgets()
        self.remove_widget(self.page)
        self.sessions = []        # dicts: {"s": session, "title": str, "term_id": int|None}
        self.panes = []
        self.active_idx = 0
        self.split = False
        self._execs = None
        self.root_box = MDBoxLayout(orientation="vertical")
        self.add_widget(self.root_box)
        self._built = False
        cfg = app.core.config
        self.font_sp = cfg.get("terminal.font_size", 12)
        self.input = TermInput(self)

    def render(self, body):
        pass          # this screen builds its own layout in build_ui()

    def refresh(self):
        pass

    @property
    def active_pane(self):
        return self.panes[min(self.active_idx, len(self.panes) - 1)] if self.panes else self._make_pane()

    def actions(self):
        return [["content-copy", lambda *_: self.copy_screen()], ["content-paste", lambda *_: self.paste()],
                ["view-split-horizontal", lambda *_: self.toggle_split()]]

    def on_pre_enter(self, *_):
        self.md_bg_color = T.c("bg")
        self.app.set_topbar(self.title, self.actions())
        if not self._built:
            self._built = True
            self._make_pane()
            self.new_console()
        self.build_ui()

    def _make_pane(self):
        p = TerminalPane(self.font_sp)
        p.on_sticky_change = self._update_sticky
        self.panes.append(p)
        return p

    # ---- sessions
    def new_console(self):
        pane = self.active_pane
        s = ConsoleSession(self.core, pane.rows, pane.cols, pane._dirty).start()
        self._add(s, s.title, None)

    def new_shell(self, profile):
        pane = self.active_pane
        proj = self.app.current_project()
        try:
            sid, s = self.core.services.get("terminal").create(profile, proj.path if proj else None,
                                                               pane.rows, pane.cols, pane._dirty)
        except Exception as e:
            message_dialog("Cannot start terminal", str(e))
            return
        self._add(s, s.title, sid)

    def _add(self, session, title, term_id):
        self.sessions.append({"s": session, "title": title, "term_id": term_id})
        self.panes[min(self.active_idx, len(self.panes) - 1)].attach(session)
        self.build_ui()

    def close_session(self, idx):
        item = self.sessions.pop(idx)
        if item["term_id"] is not None:
            self.core.services.get("terminal").close(item["term_id"])
        for p in self.panes:
            if p.session is item["s"]:
                p.session = None
                p._refresh()
        self.build_ui()

    def pick_session(self, idx):
        self.active_pane.attach(self.sessions[idx]["s"])
        self.build_ui()

    def toggle_split(self):
        self.split = not self.split
        if self.split and len(self.panes) < 2:
            p2 = self._make_pane()
            if len(self.sessions) > 1:
                p2.attach(self.sessions[-1]["s"])
        self.active_idx = 0
        self.build_ui()

    def new_menu(self):
        items = [("console-line", "NDX console (ndx ...)", self.new_console)]
        for prof in self.core.services.get("shell").profiles():
            items.append(("console", prof.title, lambda p=prof: self.new_shell(p)))
        linux = self.core.services.get("linux")
        for env in linux.list():
            def start(e=env):
                try:
                    self.new_shell(linux.profile(e["id"]))
                except Exception as ex:
                    message_dialog("Cannot start environment", str(ex))
            items.append(("layers-outline", "%s (PRoot)" % env["name"], start))
        bottom_sheet("New session", items)

    # ---- layout
    def build_ui(self):
        rb = self.root_box
        rb.clear_widgets()
        tabs = ScrollView(do_scroll_y=False, bar_width=0, size_hint_y=None, height=dp(44))
        strip = MDBoxLayout(orientation="horizontal", adaptive_width=True, spacing=dp(2), size_hint_y=1, padding=(dp(4), 0))
        for i, it in enumerate(self.sessions):
            active = self.active_pane.session is it["s"]
            strip.add_widget(text_btn(it["title"][:18], lambda i=i: self.pick_session(i), "accent" if active else "muted"))
            strip.add_widget(icon_btn("close", lambda i=i: self.close_session(i), "muted", size_hint=(None, None),
                                      size=(dp(30), dp(30))))
        strip.add_widget(icon_btn("plus", self.new_menu, "accent"))
        tabs.add_widget(strip)
        rb.add_widget(tabs)

        wide = self.width > self.height
        panes_box = MDBoxLayout(orientation="horizontal" if wide else "vertical", spacing=dp(2))
        n = 2 if self.split else 1
        for i in range(n):
            if i >= len(self.panes):
                self._make_pane()
            p = self.panes[i]
            if p.parent:
                p.parent.remove_widget(p)
            panes_box.add_widget(p)
        rb.add_widget(panes_box)
        rb.add_widget(self._suggestions_row())
        rb.add_widget(self._keys_row())
        rb.add_widget(self.input)

    def _keys_row(self):
        sc = ScrollView(do_scroll_y=False, bar_width=0, size_hint_y=None, height=dp(42))
        r = MDBoxLayout(orientation="horizontal", adaptive_width=True, spacing=dp(2), padding=(dp(4), 0))
        self.ctrl_btn = text_btn("Ctrl", lambda: self._toggle("ctrl"), "muted")
        self.alt_btn = text_btn("Alt", lambda: self._toggle("alt"), "muted")
        r.add_widget(self.ctrl_btn)
        r.add_widget(self.alt_btn)
        for name, txt in (("esc", "Esc"), ("tab", "Tab")):
            r.add_widget(text_btn(txt, lambda n=name: self.active_pane.send_key(n), "text"))
        for name, ic in (("up", "arrow-up"), ("down", "arrow-down"), ("left", "arrow-left"), ("right", "arrow-right")):
            r.add_widget(icon_btn(ic, lambda n=name: self.active_pane.send_key(n), size_hint=(None, None), size=(dp(36), dp(36))))
        for name, txt in (("home", "Home"), ("end", "End"), ("pgup", "PgUp"), ("pgdn", "PgDn")):
            r.add_widget(text_btn(txt, lambda n=name: self.active_pane.send_key(n), "text"))
        r.add_widget(text_btn("Fn", self.fn_menu, "text"))
        for m in self.core.config.get("terminal.macros", DEFAULT_MACROS):
            r.add_widget(text_btn(m["label"], lambda m=m: self.active_pane._send(combo_bytes(m["keys"])), "accent"))
        r.add_widget(icon_btn("plus-box-outline", self.add_macro_dialog, "muted", size_hint=(None, None), size=(dp(36), dp(36))))
        self.live_btn = text_btn("LIVE" if self.input.live else "line", self.toggle_live, "ok" if self.input.live else "muted")
        r.add_widget(self.live_btn)
        sc.add_widget(r)
        return sc

    def _suggestions_row(self):
        self.sugg = MDBoxLayout(orientation="horizontal", adaptive_height=True, size_hint_y=None, height=dp(0))
        return self.sugg

    def _update_sticky(self):
        p = self.active_pane
        if hasattr(self, "ctrl_btn"):
            self.ctrl_btn.text_color = T.c("accent" if p.ctrl_on else "muted")
            self.alt_btn.text_color = T.c("accent" if p.alt_on else "muted")

    def _toggle(self, which):
        p = self.active_pane
        if which == "ctrl":
            p.ctrl_on = not p.ctrl_on
        else:
            p.alt_on = not p.alt_on
        self._update_sticky()

    def toggle_live(self):
        self.input.set_live(not self.input.live)
        self.build_ui()
        self.input.focus = True

    def fn_menu(self):
        bottom_sheet("Function keys", [("keyboard-outline", "F%d" % i, lambda i=i: self.active_pane.send_key("f%d" % i))
                                       for i in range(1, 13)][:8])

    def add_macro_dialog(self):
        form_dialog("Key combination", [("label", "Button label", ""), ("keys", "Keys (e.g. ctrl+alt+x, f5)", "")],
                    self._save_macro, "Add")

    def _save_macro(self, v):
        try:
            parse_combo(v["keys"])
        except ValueError as e:
            message_dialog("Invalid combination", str(e))
            return
        macros = self.core.config.get("terminal.macros", DEFAULT_MACROS)
        macros.append({"label": v["label"][:6] or v["keys"], "keys": v["keys"]})
        self.core.config.set("terminal.macros", macros)
        self.build_ui()

    # ---- line mode helpers
    def submit_line(self, text):
        s = self.active_pane.session
        if s is None:
            return
        if hasattr(s, "history") and text.strip():
            s.history.append(text)
        s.write(text + "\r")
        self.input.hist_pos = None
        self._show_suggestions([])

    def history_step(self, inp, step):
        s = self.active_pane.session
        hist = getattr(s, "history", [])
        if not hist:
            return
        pos = len(hist) if inp.hist_pos is None else inp.hist_pos
        pos = max(0, min(len(hist), pos + step))
        inp.hist_pos = pos
        inp.text = hist[pos] if pos < len(hist) else ""

    def tab_complete(self, inp):
        cands = self._candidates(inp.text)
        if len(cands) == 1:
            inp.text = inp.text.rsplit(" ", 1)[0] + " " + cands[0] if " " in inp.text else cands[0]
        self._show_suggestions(cands)

    def _candidates(self, text):
        s = self.active_pane.session
        hist = [h for h in reversed(getattr(s, "history", [])) if h.startswith(text) and h != text]
        out = list(dict.fromkeys(hist))[:3]
        if " " not in text and text:
            if self._execs is None:
                self._execs = _path_executables()
            out += [e for e in self._execs if e.startswith(text)][:6]
        else:
            frag = text.rsplit(" ", 1)[-1]
            d, base = os.path.split(frag)
            try:
                out += [os.path.join(d, f) for f in sorted(os.listdir(d or ".")) if f.startswith(base)][:6]
            except OSError:
                pass
        return out[:8]

    def _show_suggestions(self, cands):
        self.sugg.clear_widgets()
        self.sugg.height = dp(34) if cands else 0
        for c in cands:
            self.sugg.add_widget(text_btn(c[-22:], lambda c=c: self._take(c), "text"))

    def _take(self, c):
        t = self.input.text
        self.input.text = c if " " not in t or c in [h for h in getattr(self.active_pane.session, "history", [])] \
            else t.rsplit(" ", 1)[0] + " " + c
        self._show_suggestions([])

    # ---- clipboard
    def copy_screen(self):
        s = self.active_pane.session
        if s is not None:
            Clipboard.copy("\n".join(s.screen.text_lines()).rstrip())
            toast("Screen copied")

    def paste(self):
        txt = Clipboard.paste()
        if txt:
            self.active_pane.send_text(txt)
