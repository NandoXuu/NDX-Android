from kivy.clock import Clock
from kivymd.uix.screen import MDScreen

from ..theme import T
from ..widgets import Page


class NDXScreen(MDScreen):
    key = "screen"
    title = "Screen"
    refresh_interval = 0          # seconds; 0 = no periodic refresh

    def __init__(self, app, **kw):
        super().__init__(name=self.key, md_bg_color=T.c("bg"), **kw)
        self.app = app
        self.page = Page()
        self.add_widget(self.page)
        self._ev = None

    @property
    def core(self):
        return self.app.core

    def on_pre_enter(self, *_):
        self.md_bg_color = T.c("bg")
        self.rebuild()
        self.app.set_topbar(self.title, self.actions())
        if self.refresh_interval:
            self._ev = Clock.schedule_interval(lambda dt: self.refresh(), self.refresh_interval)

    def on_leave(self, *_):
        if self._ev:
            self._ev.cancel()
            self._ev = None

    def rebuild(self):
        self.render(self.page.reset())

    def refresh(self):
        self.rebuild()

    def render(self, body):
        raise NotImplementedError

    def actions(self):
        return []
