"""Terminal view and input widgets (Kivy). The emulation itself lives in ndx.terminal."""
import os

from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivymd.uix.boxlayout import MDBoxLayout

from ..terminal import encode_key
from .theme import T

KEYMAP = {"enter": "enter", "backspace": "backspace", "tab": "tab", "escape": "esc", "up": "up", "down": "down",
          "left": "left", "right": "right", "home": "home", "end": "end", "pageup": "pgup", "pagedown": "pgdn",
          "delete": "del", "insert": "ins", "numpadenter": "enter"}
KEYMAP.update({"f%d" % i: "f%d" % i for i in range(1, 13)})


def measure(font_size):
    cl = CoreLabel(font_name="NDXMono", font_size=font_size)
    w, h = cl.get_extents("M")
    return max(1, w), max(1, h)


class TerminalPane(MDBoxLayout):
    """Displays one session. Sizes itself in character cells and informs the PTY."""

    def __init__(self, font_size_sp=12, **kw):
        super().__init__(orientation="vertical", **kw)
        self.session = None
        self.font_px = sp(font_size_sp)
        self.rows, self.cols = 24, 80
        self.ctrl_on = self.alt_on = False
        self.on_sticky_change = lambda: None
        self._pending = False
        with self.canvas.before:
            self._bg_color = Color(*T.c("bg"))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.view = Label(markup=True, font_name="NDXMono", font_size=self.font_px, halign="left", valign="top",
                          color=T.c("text"), size_hint=(1, 1))
        self.add_widget(self.view)
        self.bind(pos=self._layout, size=self._layout)

    def _layout(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        cw, ch = measure(self.font_px)
        cols, rows = max(20, int(self.width // cw)), max(5, int(self.height // ch))
        self.view.text_size = (self.width, self.height)
        if (rows, cols) != (self.rows, self.cols):
            self.rows, self.cols = rows, cols
            if self.session:
                self.session.resize(rows, cols)
        self._refresh()

    def apply_theme(self):
        self._bg_color.rgba = T.c("bg")
        self.view.color = T.c("text")
        self._refresh()

    def attach(self, session):
        self.session = session
        session.on_update = self._dirty
        session.resize(self.rows, self.cols)
        self._refresh()

    def _dirty(self):
        if not self._pending:
            self._pending = True
            Clock.schedule_once(self._refresh, 0.04)

    def _refresh(self, *_):
        self._pending = False
        if self.session:
            self.view.text = self.session.snapshot_markup(default_fg=T.hex("text"), default_bg=T.hex("bg"))
        else:
            self.view.text = "[color=%s]No session. Use + to start one.[/color]" % T.hex("muted")

    # ---- input
    def _send(self, data):
        if self.session is not None and data:
            self.session.write(data)

    def send_text(self, text):
        if self.ctrl_on or self.alt_on:
            out = b"".join(encode_key(ch, ctrl=self.ctrl_on, alt=self.alt_on) for ch in text)
            self.ctrl_on = self.alt_on = False
            self.on_sticky_change()
            self._send(out)
        else:
            self._send(text.replace("\n", "\r"))

    def send_key(self, name, ctrl=False, alt=False, shift=False):
        app_cursor = bool(self.session and getattr(self.session, "screen", None) and self.session.screen.app_cursor)
        self._send(encode_key(name, ctrl or self.ctrl_on, alt or self.alt_on, shift, app_cursor))
        if self.ctrl_on or self.alt_on:
            self.ctrl_on = self.alt_on = False
            self.on_sticky_change()


class TermInput(TextInput):
    """Single-line input. Line mode: Enter sends the line. Live mode: every key goes
    straight to the program (needed for vim, nano, htop...)."""

    def __init__(self, screen, **kw):
        super().__init__(multiline=False, font_name="NDXMono", size_hint_y=None, height=dp(44),
                         background_color=T.c("surface"), foreground_color=T.c("text"),
                         cursor_color=T.c("accent"), hint_text_color=T.c("muted"),
                         hint_text="command", write_tab=False, **kw)
        self.screen_ref = screen
        self.live = False
        self.hist_pos = None

    def set_live(self, live):
        self.live = live
        self.text = ""
        self.hint_text = "live: keys go straight to the program" if live else "command"

    def insert_text(self, substring, from_undo=False):
        if self.live:
            self.screen_ref.active_pane.send_text(substring)
            return
        return super().insert_text(substring, from_undo=from_undo)

    def keyboard_on_key_down(self, window, keycode, text, modifiers):
        key = keycode[1]
        pane = self.screen_ref.active_pane
        if self.live:
            if key in KEYMAP:
                pane.send_key(KEYMAP[key], "ctrl" in modifiers, "alt" in modifiers, "shift" in modifiers)
                return True
            if text and ("ctrl" in modifiers or "alt" in modifiers):
                pane.send_key(text, "ctrl" in modifiers, "alt" in modifiers)
                return True
            return super().keyboard_on_key_down(window, keycode, text, modifiers)
        if key == "up":
            self.screen_ref.history_step(self, -1)
            return True
        if key == "down":
            self.screen_ref.history_step(self, 1)
            return True
        if key == "tab":
            self.screen_ref.tab_complete(self)
            return True
        return super().keyboard_on_key_down(window, keycode, text, modifiers)

    def on_text_validate(self):
        self.screen_ref.submit_line(self.text)
        self.text = ""
        Clock.schedule_once(lambda dt: setattr(self, "focus", True), 0.05)
