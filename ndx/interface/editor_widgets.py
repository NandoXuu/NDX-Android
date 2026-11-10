"""Code editor widgets: highlighted input with auto-indent + line-number gutter."""
from kivy.metrics import dp
from kivy.uix.codeinput import CodeInput
from kivy.uix.label import Label
from kivy.uix.stencilview import StencilView
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from ..utilities import match_bracket
from .theme import T


def lexer_for(alias):
    try:
        return get_lexer_by_name(alias or "text")
    except ClassNotFound:
        return get_lexer_by_name("text")


class NDXCodeInput(CodeInput):
    """Syntax-highlighted editor. Auto-indents on Enter (and after ':' / '{' / '(')."""

    def __init__(self, tab_width=4, **kw):
        kw.setdefault("font_name", "NDXMono")
        kw.setdefault("background_color", T.c("surface"))
        kw.setdefault("foreground_color", T.c("text"))
        kw.setdefault("cursor_color", T.c("accent"))
        kw.setdefault("line_spacing", 0)
        kw.setdefault("padding", (dp(8), dp(8), dp(8), dp(8)))
        super().__init__(**kw)
        self.tab_width_cfg = tab_width
        self.use_bubble = True
        self.bind(on_double_tap=lambda *_: None)

    def set_language(self, alias):
        self.lexer = lexer_for(alias)

    def set_style(self, name):
        try:
            self.style_name = name
        except Exception:
            self.style_name = "monokai"
        self.background_color = T.c("surface")

    def insert_text(self, substring, from_undo=False):
        if substring == "\n" and not from_undo:
            row = self.cursor_row
            line = self._lines[row] if row < len(self._lines) else ""
            before = line[:self.cursor_col]
            indent = line[:len(line) - len(line.lstrip(" \t"))]
            if before.rstrip().endswith((":", "{", "(", "[")):
                indent += " " * self.tab_width_cfg
            substring = "\n" + indent
        return super().insert_text(substring, from_undo=from_undo)

    def do_tab(self):
        self.insert_text(" " * self.tab_width_cfg)

    def goto_line(self, line, col=0):
        line = max(1, min(line, len(self._lines)))
        self.cursor = (col, line - 1)

    def bracket_info(self):
        """(row, col) of the bracket matching the one at/before the cursor, or None."""
        text = self.text
        idx = self.cursor_index()
        for i in (idx, idx - 1):
            j = match_bracket(text, i)
            if j >= 0:
                return self.get_cursor_from_index(j)
        return None

    def find_from_cursor(self, needle, backwards=False):
        if not needle:
            return False
        text = self.text
        start = self.cursor_index()
        if backwards:
            i = text.rfind(needle, 0, max(0, start - 1))
        else:
            i = text.find(needle, start)
            if i < 0:
                i = text.find(needle)           # wrap around
        if i < 0:
            return False
        self.cursor = self.get_cursor_from_index(i + len(needle))
        self.select_text(i, i + len(needle))
        return True


class LineGutter(StencilView):
    """Line numbers kept in sync with the editor's scroll position."""

    def __init__(self, code, **kw):
        super().__init__(size_hint_x=None, width=dp(44), **kw)
        self.code = code
        self.lbl = Label(markup=False, font_name="NDXMono", halign="right", valign="top",
                         color=T.c("muted"), size_hint=(None, None))
        self.add_widget(self.lbl)
        code.bind(text=self._update, scroll_y=self._sync, font_size=self._update, height=self._sync)
        self.bind(pos=self._sync, size=self._sync)
        self._update()

    def _update(self, *_):
        n = max(1, len(self.code._lines))
        self.width = dp(14) + dp(9) * max(3, len(str(n))) * (self.code.font_size / dp(14))
        self.lbl.font_size = self.code.font_size
        self.lbl.text = "\n".join(str(i) for i in range(1, n + 1))
        self.lbl.text_size = (self.width - dp(8), None)
        self.lbl.texture_update()
        self.lbl.size = (self.width - dp(8), self.lbl.texture_size[1])
        self._sync()

    def _sync(self, *_):
        pad_top = self.code.padding[1] if len(self.code.padding) > 1 else 0
        self.lbl.x = self.x
        self.lbl.top = self.top - pad_top + self.code.scroll_y
