"""Reusable KivyMD 1.2.0 widgets and dialog helpers."""
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.gridlayout import GridLayout
from kivy.uix.modalview import ModalView
from kivy.uix.spinner import Spinner
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDIconButton, MDRaisedButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField

from .theme import T


def label(text, style="Body1", color="text", **kw):
    kw.setdefault("adaptive_height", True)
    kw.setdefault("markup", True)
    return MDLabel(text=text, font_style=style, theme_text_color="Custom", text_color=T.c(color), **kw)


def icon(name, color="muted", size=24):
    return MDIcon(icon=name, theme_text_color="Custom", text_color=T.c(color), font_size=sp(size),
                  size_hint=(None, None), size=(dp(size + 8), dp(size + 8)), halign="center")


def card(orientation="vertical", **kw):
    kw.setdefault("padding", dp(14))
    kw.setdefault("spacing", dp(8))
    kw.setdefault("adaptive_height", True)
    return MDCard(orientation=orientation, md_bg_color=T.c("surface"), radius=[dp(16)] * 4,
                  line_color=T.c("border"), elevation=0, **kw)


def column(**kw):
    kw.setdefault("adaptive_height", True)
    kw.setdefault("spacing", dp(10))
    return MDBoxLayout(orientation="vertical", **kw)


def row(**kw):
    kw.setdefault("adaptive_height", True)
    kw.setdefault("spacing", dp(8))
    return MDBoxLayout(orientation="horizontal", **kw)


def primary_btn(text, cb, **kw):
    return MDRaisedButton(text=text, md_bg_color=T.c("accent"), theme_text_color="Custom",
                          text_color=T.c("on_accent"), on_release=lambda *_: cb(), **kw)


def text_btn(text, cb, color="accent", **kw):
    return MDFlatButton(text=text, theme_text_color="Custom", text_color=T.c(color),
                        on_release=lambda *_: cb(), **kw)


def icon_btn(name, cb, color="text", **kw):
    return MDIconButton(icon=name, theme_icon_color="Custom", icon_color=T.c(color),
                        on_release=lambda *_: cb(), **kw)


def field(hint, text="", **kw):
    f = MDTextField(hint_text=hint, text=text, mode="rectangle", size_hint_y=None, height=dp(56), **kw)
    return f


def chip(text, color="muted"):
    c = MDCard(md_bg_color=T.c("surface2"), radius=[dp(12)] * 4, size_hint=(None, None), height=dp(24),
               padding=(dp(10), 0), elevation=0)
    lbl = label(text, "Caption", color, adaptive_height=False, adaptive_width=True, halign="center", valign="middle")
    lbl.size_hint_y = 1
    c.add_widget(lbl)
    c.width = dp(24) + len(text) * dp(6.5)
    return c


def divider():
    from kivy.uix.widget import Widget
    from kivy.graphics import Color, Rectangle
    w = Widget(size_hint_y=None, height=dp(1))
    with w.canvas:
        Color(*T.c("border"))
        r = Rectangle(pos=w.pos, size=w.size)
    w.bind(pos=lambda i, v: setattr(r, "pos", v), size=lambda i, v: setattr(r, "size", v))
    return w


class ResponsiveGrid(GridLayout):
    """Grid whose column count follows the available width (dp-based)."""

    def __init__(self, min_item_dp=300, **kw):
        kw.setdefault("spacing", dp(12))
        super().__init__(cols=1, size_hint_y=None, **kw)
        self.min_item = dp(min_item_dp)
        self.bind(minimum_height=self.setter("height"), width=self._relayout)

    def _relayout(self, *_):
        n = max(1, int(self.width // self.min_item))
        if n != self.cols:
            self.cols = n


class NavItem(ButtonBehavior, MDBoxLayout):
    def __init__(self, key, icon_name, text, on_press_cb, compact=False, **kw):
        super().__init__(orientation="vertical", adaptive_height=compact, padding=(dp(4), dp(6)),
                         spacing=dp(2), size_hint_y=None, height=dp(64), **kw)
        self.key = key
        self.icon_w = icon(icon_name, "muted", 24)
        self.icon_w.pos_hint = {"center_x": .5}
        self.text_w = label(text, "Caption", "muted", halign="center", shorten=True, shorten_from="right")
        self.add_widget(self.icon_w)
        self.add_widget(self.text_w)
        self._cb = on_press_cb
        self.bind(on_release=lambda *_: self._cb(self.key))

    def set_active(self, active):
        col = T.c("accent") if active else T.c("muted")
        self.icon_w.text_color = col
        self.text_w.text_color = col


def toast(text):
    try:
        from kivymd.uix.snackbar import MDSnackbar
        MDSnackbar(MDLabel(text=text, theme_text_color="Custom", text_color=T.c("text")),
                   md_bg_color=T.c("surface2"), y=dp(24), pos_hint={"center_x": .5}, size_hint_x=.92).open()
    except Exception:
        pass


def _dialog(title, content=None, text=None, buttons=None):
    kw = dict(title=title, buttons=buttons or [])
    if content is not None:
        kw.update(type="custom", content_cls=content)
    else:
        kw["text"] = text or ""
    d = MDDialog(**kw)
    d.open()
    return d


def confirm_dialog(title, text, on_yes, yes_text="Confirm", danger=False):
    holder = {}

    def yes(*_):
        holder["d"].dismiss()
        on_yes()

    holder["d"] = _dialog(title, text=text, buttons=[
        text_btn("Cancel", lambda: holder["d"].dismiss(), "muted"),
        MDFlatButton(text=yes_text, theme_text_color="Custom",
                     text_color=T.c("error" if danger else "accent"), on_release=yes)])
    return holder["d"]


def message_dialog(title, text):
    holder = {}
    holder["d"] = _dialog(title, text=text, buttons=[text_btn("Close", lambda: holder["d"].dismiss())])
    return holder["d"]


def form_dialog(title, fields, on_ok, ok_text="OK", extra=None):
    """fields: list of (key, hint, default). extra: list of (key, [options], default) Spinners."""
    box = MDBoxLayout(orientation="vertical", spacing=dp(12), adaptive_height=True, padding=(0, dp(8), 0, 0))
    widgets = {}
    for key, hint, default in fields:
        w = field(hint, default)
        widgets[key] = w
        box.add_widget(w)
    for key, options, default in (extra or []):
        sp_ = Spinner(text=default, values=options, size_hint_y=None, height=dp(48),
                      background_color=T.c("surface2"), color=T.c("text"))
        widgets[key] = sp_
        box.add_widget(sp_)
    holder = {}

    def ok(*_):
        values = {k: w.text for k, w in widgets.items()}
        holder["d"].dismiss()
        on_ok(values)

    holder["d"] = _dialog(title, content=box, buttons=[
        text_btn("Cancel", lambda: holder["d"].dismiss(), "muted"),
        MDFlatButton(text=ok_text, theme_text_color="Custom", text_color=T.c("accent"), on_release=ok)])
    return holder["d"]


def bottom_sheet(title, items):
    """items: list of (icon, text, callback). Plain Kivy ModalView (no extra deps)."""
    mv = ModalView(size_hint=(1, None), height=dp(56 + 56 * len(items)), pos_hint={"bottom": 1},
                   background_color=(0, 0, 0, .5), background="")
    body = MDCard(orientation="vertical", md_bg_color=T.c("surface"), radius=[dp(20), dp(20), 0, 0],
                  padding=dp(8), elevation=0)
    body.add_widget(label(title, "Subtitle1", padding=(dp(12), dp(8)), size_hint_y=None, height=dp(48)))
    for ic, text, cb in items:
        r = NavItem(text, ic, text, lambda k, cb=cb: (mv.dismiss(), cb()), compact=False)
        r.orientation = "horizontal"
        r.height = dp(52)
        r.clear_widgets()
        r.add_widget(icon(ic, "accent"))
        r.add_widget(label(text, "Body1", valign="middle", adaptive_height=False))
        body.add_widget(r)
    mv.add_widget(body)
    mv.open()
    return mv


class Page(MDBoxLayout):
    """Vertical scrolling page body used by most screens."""

    def __init__(self, **kw):
        super().__init__(orientation="vertical", **kw)
        self.scroll = MDScrollView(do_scroll_x=False)
        self.content = column(padding=(dp(14), dp(12)), spacing=dp(14))
        self.scroll.add_widget(self.content)
        self.add_widget(self.scroll)

    def reset(self):
        self.content.clear_widgets()
        return self.content
