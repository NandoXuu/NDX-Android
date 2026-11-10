#!/usr/bin/env python3
"""Headless smoke test of the Kivy/KivyMD interface code using stub widget classes.

Catches NameError/AttributeError/typos in NDX's own UI code. It does NOT validate real
Kivy/KivyMD behaviour or layout: that needs a device or desktop Kivy run (see docs/BUILD.md)."""
import importlib.abc
import importlib.machinery
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _Canvas:
    def __init__(self):
        self.before = self
        self.after = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class W:
    def __init__(self, *a, **kw):
        self.children, self.parent = [], None
        self.canvas = _Canvas()
        self.__dict__.update(kw)
        self.pos, self.size, self.width, self.height = (0, 0), (0, 0), 100, 100
        self.x = self.y = self.top = 0
        self.text = kw.get("text", "")
        self.cursor = (0, 0)
        self._lines = [""]
        self.scroll_y = 0
        self.texture_size = (0, 0)
        self.padding = kw.get("padding", (0, 0, 0, 0))
        self.font_size = kw.get("font_size", 14)

    def add_widget(self, w, *a, **k):
        self.children.append(w)
        try:
            w.parent = self
        except Exception:
            pass

    def remove_widget(self, w):
        if w in self.children:
            self.children.remove(w)
        w.parent = None

    def clear_widgets(self, *a):
        self.children = []

    def bind(self, **kw):
        pass

    def setter(self, name):
        return lambda *a: None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: 0

    def cursor_index(self):
        return 0

    def find(self, *a):
        return None


class _Clock:
    def schedule_once(self, fn, t=0):
        return types.SimpleNamespace(cancel=lambda: None)

    def schedule_interval(self, fn, t):
        return types.SimpleNamespace(cancel=lambda: None)


class _Win(W):
    width = 400
    height = 800


def _hex(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]


class Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        special = {"dp": lambda x: x, "sp": lambda x: x, "Clock": _Clock(), "Window": _Win(),
                   "get_color_from_hex": _hex, "Clipboard": W(), "LabelBase": W()}
        if name in special:
            return special[name]
        cls = type(name, (W,), {})
        if name == "Label" and self.__name__.endswith("core.text"):
            cls = type("CoreLabel", (W,), {"get_extents": lambda s, t: (8, 16)})
        setattr(self, name, cls)
        return cls


class Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in ("kivy", "kivymd"):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)

    def create_module(self, spec):
        m = Stub(spec.name)
        m.__path__ = []
        return m

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, Finder())
os.environ["NDX_HOME"] = tempfile.mkdtemp()

from ndx.interface.app import NDXApp  # noqa: E402
from ndx.interface import screens as S  # noqa: E402


def main():
    app = NDXApp()
    app.build()
    app.on_start()
    for scr in app.screens.values():
        scr.on_pre_enter()
        scr.actions()
        if hasattr(scr, "refresh"):
            scr.refresh()
        print("ok  ", scr.key)
    # editor with a real project/file
    pm = app.core.services.get("projects")
    proj = pm.create("SmokeProj", "python-hello")
    app.open_project(proj)
    ed = app.screens["editor"]
    ed.on_pre_enter()
    ed.open_file(os.path.join(proj.path, "main.py"))
    ed.build_ui()
    ed.toggle_problems()
    ed.toggle_output()
    ed._update_status()
    ed.save()
    ed.run_project()
    print("ok   editor with file")
    # exercise a few interactive paths
    app.go("terminal")
    t = app.screens["terminal"]
    t.new_menu()
    t.submit_line("help")
    app.screens["projects"].new_dialog()
    app.screens["packages"].doctor()
    app.more_menu()
    app.set_theme(dark=False)
    app.set_theme(dark=True, accent="Blue")
    app.on_stop()
    print("UI SMOKE OK")


if __name__ == "__main__":
    main()
