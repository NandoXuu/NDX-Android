"""Embedded Kivy GUI sessions inside the existing NDX application."""
import os
import runpy
import sys
import threading
import traceback

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.toolbar import MDTopAppBar

from kivy.app import App as KivyApp

from .embedded import _install


class EmbeddedKivySession:
    """Runs a user's Kivy App inside NDX without starting another event loop."""

    def __init__(self, ndx_app):
        self.ndx_app = ndx_app
        self.runtime = ndx_app.core.services.get("python")
        self.active = False
        self.mounted = False
        self.project_app = None
        self.host = None
        self.root = None
        self.saved_children = []
        self.saved_running_app = None
        self.saved_methods = {}
        self.saved_app_window = None
        self.saved_built = None
        self.saved_state = None
        self.script = None
        self.cwd = None
        self.label = "run"
        self._lock = None
        self._out_router = None
        self._err_router = None
        self._thread_id = None
        self._hooks_installed = False
        self._stopping = False

    def _emit(self, text):
        if text:
            self.ndx_app.core.bus.publish(
                "run.output",
                {"label": self.label, "text": str(text)},
                "kivy-gui"
            )

    def _save_environment(self):
        self.saved_state = {
            "cwd": os.getcwd(),
            "argv": list(sys.argv),
            "path": list(sys.path),
            "env": dict(os.environ),
            "modules": set(sys.modules),
        }

        os.chdir(self.cwd)
        sys.argv = [self.script]
        sys.path.insert(0, os.path.dirname(self.script))

    def _restore_environment(self):
        state = self.saved_state
        if not state:
            return

        try:
            os.chdir(state["cwd"])
        except OSError:
            pass

        sys.argv = state["argv"]
        sys.path[:] = state["path"]
        os.environ.clear()
        os.environ.update(state["env"])

        root = os.path.dirname(self.script)
        for name in set(sys.modules) - state["modules"]:
            module = sys.modules.get(name)
            filename = getattr(module, "__file__", None) or ""
            if filename.startswith(root):
                sys.modules.pop(name, None)

        self.saved_state = None

    def _install_hooks(self):
        self.saved_methods = {
            name: KivyApp.__dict__.get(name)
            for name in ("run", "stop", "_stop")
        }

        session = self

        def run_intercept(project_app, *args, **kwargs):
            session.mount(project_app)

        def stop_intercept(project_app, *args, **kwargs):
            session.stop()

        KivyApp.run = run_intercept
        KivyApp.stop = stop_intercept
        KivyApp._stop = stop_intercept
        self._hooks_installed = True

    def _restore_hooks(self):
        if not self._hooks_installed:
            return
        for name, original in self.saved_methods.items():
            if original is None:
                try:
                    delattr(KivyApp, name)
                except AttributeError:
                    pass
            else:
                setattr(KivyApp, name, original)

        self.saved_methods.clear()
        KivyApp._running_app = self.saved_running_app
        self._hooks_installed = False

    def mount(self, project_app):
        if self.mounted:
            raise RuntimeError("A Kivy project is already mounted.")

        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Kivy GUI must be mounted on the main thread.")

        self.project_app = project_app

        project_app.load_config()
        project_app.load_kv(filename=project_app.kv_file)

        if not project_app.root:
            project_app.root = project_app.build()

        if not isinstance(project_app.root, Widget):
            raise TypeError(
                "Kivy App.build() must return a Widget, not %s."
                % type(project_app.root).__name__
            )

        self.saved_built = getattr(project_app, "built", False)
        project_app.built = True
        self.root = project_app.root

        self.saved_app_window = getattr(project_app, "_app_window", None)
        project_app._app_window = Window

        self.saved_children = list(reversed(self.ndx_app.root_box.children))
        self.ndx_app.root_box.clear_widgets()

        self.host = MDBoxLayout(
            orientation="vertical",
            spacing=0
        )

        self.host.add_widget(
            MDTopAppBar(
                title="Running: " + os.path.basename(self.script),
                elevation=0,
                right_action_items=[
                    ["stop", lambda *_: self.stop()]
                ]
            )
        )

        self.host.add_widget(self.root)
        self.ndx_app.root_box.add_widget(self.host)

        self.mounted = True
        self.active = True

        project_app.dispatch("on_start")
        self._emit("[NDX] Kivy GUI started.\n")

    def start(self, script, cwd, label="run"):
        if self.active:
            raise RuntimeError("A Kivy GUI session is already active.")

        script = os.path.realpath(str(script))
        if not os.path.isfile(script):
            raise FileNotFoundError("Script not found: " + script)

        if self.runtime is None or not hasattr(self.runtime, "embedded"):
            raise RuntimeError("Python Runtime embedded não está disponível.")
        runner = self.runtime.embedded
        if not runner._run_lock.acquire(blocking=False):
            raise RuntimeError("The embedded Python runner is busy.")

        self._lock = runner._run_lock
        self.script = script
        self.cwd = os.path.realpath(str(cwd or os.path.dirname(script)))
        self.label = label

        try:
            self._thread_id = threading.get_ident()
            self._out_router, self._err_router = _install()

            self._out_router.sinks[self._thread_id] = (
                lambda value: self._emit(value)
            )
            self._err_router.sinks[self._thread_id] = (
                lambda value: self._emit(value)
            )

            self._save_environment()
            self.saved_running_app = KivyApp._running_app
            self.runtime.audit(
                "python.run", target="embedded",
                script=os.path.basename(script), caller="user"
            )
            self._install_hooks()
            self.active = True

            self._emit("$ python3 %s [embedded GUI]\n" % script)
            Clock.schedule_once(self._execute, 0)
            return True

        except Exception:
            self._cleanup()
            raise

    def _execute(self, _dt):
        if not self.active:
            return

        try:
            runpy.run_path(self.script, run_name="__main__")

            if not self.mounted:
                self._emit(
                    "[NDX] Script terminou sem abrir uma interface Kivy.\n"
                )
                self.stop()

        except BaseException:
            self._emit(traceback.format_exc())
            self.stop()

    def stop(self):
        if not self.active or self._stopping:
            return

        self._stopping = True
        project_app = self.project_app

        try:
            if project_app is not None and self.mounted:
                try:
                    project_app.dispatch("on_stop")
                except Exception:
                    self._emit(traceback.format_exc())

            if self.host is not None:
                if self.host.parent is not None:
                    self.ndx_app.root_box.remove_widget(self.host)
                self.host.clear_widgets()
                self.host = None

            if self.root is not None and self.root.parent is not None:
                self.root.parent.remove_widget(self.root)

            if self.mounted:
                self.ndx_app.root_box.clear_widgets()
                for widget in self.saved_children:
                    self.ndx_app.root_box.add_widget(widget)

            if project_app is not None:
                project_app.root = None
                project_app._app_window = self.saved_app_window
                project_app.built = self.saved_built

            self.mounted = False
            self.active = False
            self.project_app = None
            self.root = None
            self.saved_children = []

            self._cleanup()
            self._emit("[NDX] Kivy GUI stopped. Returned to editor.\n")

        finally:
            self._stopping = False

    def _cleanup(self):
        self._restore_hooks()

        if self._out_router is not None:
            self._out_router.sinks.pop(self._thread_id, None)

        if self._err_router is not None:
            self._err_router.sinks.pop(self._thread_id, None)

        self._restore_environment()

        if self._lock is not None:
            self._lock.release()
            self._lock = None

        self._thread_id = None
        self._out_router = None
        self._err_router = None
