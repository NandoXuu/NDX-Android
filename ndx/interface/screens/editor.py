import os

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView
from kivymd.uix.boxlayout import MDBoxLayout

from ...language_support import complete, diagnose
from ...language_support.completion import current_prefix
from ...compiler_manager import expand_command
from ..editor_session import EditorSession
from ..editor_widgets import NDXCodeInput, LineGutter
from ..theme import T
from ..widgets import (card, chip, column, confirm_dialog, form_dialog, icon, icon_btn, label, message_dialog,
                       primary_btn, row, text_btn, toast, NavItem)
from .base import NDXScreen

IGNORED = {".git", ".ndx", "__pycache__", "node_modules"}


class EditorScreen(NDXScreen):
    key = "editor"
    title = "Editor"

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        self.session = EditorSession(app.core.config, app.core.services.get("languages"))
        self.project = None
        self.explorer_open = False
        self.expanded = set()
        self.code = None
        self._diag_ev = None
        self._loading = False
        self.diags = []
        self.output_lines = []
        self.show_output = False
        self.show_problems = False
        self._built = False
        self.page.clear_widgets()
        self.root_box = MDBoxLayout(orientation="vertical")
        self.add_widget(self.root_box)
        self.remove_widget(self.page)
        core = app.core
        core.bus.subscribe("run.output", self._on_run_output, on_main=True)
        core.bus.subscribe("run.finished", self._on_run_done, on_main=True)

    def render(self, body):
        pass          # this screen builds its own layout in build_ui()

    def refresh(self):
        pass

    # ---- lifecycle
    def on_pre_enter(self, *_):
        self.md_bg_color = T.c("bg")
        if not self._built:
            self.session.restore()
            self._built = True
        self.app.set_topbar(self.title, self.actions())
        self.build_ui()

    def on_leave(self, *_):
        self._sync_text()
        self.session.persist()

    def actions(self):
        items = [["content-save-outline", lambda *_: self.save()],
                 ["play-outline", lambda *_: self.run_project()],
                 ["magnify", lambda *_: self.find_dialog()]]
        from ...core.paths import is_android
        if is_android():
            items[0:0] = [["folder-open-outline", lambda *_: self.import_external_file()],
                          ["content-save-edit-outline", lambda *_: self.save_as_external()]]
        return items

    def import_external_file(self):
        """Import a SAF document into app-private storage, then open it in the editor."""
        try:
            self.app.get_saf().open_document(self._on_external_file, "*/*")
        except Exception as e:
            message_dialog("Cannot open file picker", str(e))

    def _on_external_file(self, result):
        if not result.get("ok"):
            return
        from pathlib import Path
        import re, uuid
        try:
            saf = self.app.get_saf()
            info = saf.query_document(result["uri"])
            name = info.get("name") or ("import-" + uuid.uuid4().hex[:8])
            name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "imported-file"
            folder = self.core.paths.data / "imports"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / name
            if target.exists():
                target = folder / (target.stem + "-" + uuid.uuid4().hex[:6] + target.suffix)
            saf.copy_to_private(result["uri"], target)
            self.open_file(str(target))
            self.build_ui()
            toast("Imported " + target.name)
        except Exception as e:
            message_dialog("Cannot import file", str(e))

    def save_as_external(self):
        doc = self.session.current()
        if doc is None:
            message_dialog("No file open", "Open a file before using Save as.")
            return
        self._sync_text()
        # Persist the current editor buffer before the asynchronous picker opens;
        # Save as must export the latest edits, not the previous on-disk version.
        self.session.save(doc)
        import mimetypes
        mime = mimetypes.guess_type(doc.name)[0] or "text/plain"
        try:
            self.app.get_saf().create_document(
                lambda result, path=doc.path: self._on_save_as_created(result, path),
                mime_type=mime, suggested_name=doc.name
            )
        except Exception as e:
            message_dialog("Cannot open Save as picker", str(e))

    def _on_save_as_created(self, result, source_path):
        if not result.get("ok"):
            return
        try:
            self.app.get_saf().copy_from_private(source_path, result["uri"])
            toast("Exported " + os.path.basename(source_path))
        except Exception as e:
            message_dialog("Cannot export file", str(e))

    def open_project(self, project):
        self.project = project
        self.core.config.set("session.last_project", project.path)
        self.expanded = {project.path}

    def open_file(self, path):
        self._sync_text()
        try:
            self.session.open(path)
        except (OSError, ValueError) as e:
            message_dialog("Cannot open file", str(e))
        self.session.persist()

    # ---- UI construction
    def build_ui(self):
        self.root_box.clear_widgets()
        cfg = self.core.config
        top = row(size_hint_y=None, height=dp(44), padding=(dp(6), 0))
        top.add_widget(icon_btn("file-tree-outline", self.toggle_explorer))
        tabs_scroll = ScrollView(do_scroll_y=False, bar_width=0, size_hint_y=None, height=dp(44))
        tabs = MDBoxLayout(orientation="horizontal", adaptive_width=True, spacing=dp(4), size_hint_y=1)
        for i, d in enumerate(self.session.docs):
            active = i == self.session.active
            t = NavItem(str(i), "circle-small" if d.modified else "file-outline", d.name,
                        lambda k: self.switch(int(k)), compact=False)
            t.orientation = "horizontal"
            t.size_hint = (None, 1)
            t.width = dp(40) + dp(9) * min(len(d.name), 18)
            t.clear_widgets()
            t.add_widget(icon("circle-small" if d.modified else "file-outline", "accent" if active else "muted", 18))
            t.add_widget(label(d.name, "Caption", "accent" if active else "text", adaptive_height=False,
                               valign="middle", shorten=True, shorten_from="right"))
            tabs.add_widget(t)
            tabs.add_widget(icon_btn("close", lambda i=i: self.close_tab(i), "muted", size_hint=(None, None),
                                     size=(dp(32), dp(32))))
        tabs_scroll.add_widget(tabs)
        top.add_widget(tabs_scroll)
        self.root_box.add_widget(top)

        body = MDBoxLayout(orientation="horizontal")
        wide = self.width >= dp(720)
        self.explorer_open = self.explorer_open or False
        if self.explorer_open and wide:
            body.add_widget(self._explorer_panel())
        main = MDBoxLayout(orientation="vertical")
        doc = self.session.current()
        if doc is None:
            main.add_widget(self._empty_state())
        else:
            main.add_widget(self._editor_area(doc))
            main.add_widget(self._suggestion_bar())
            main.add_widget(self._toolbar())
            if self.show_problems:
                main.add_widget(self._problems_panel())
            if self.show_output:
                main.add_widget(self._output_panel())
            main.add_widget(self._statusbar(doc))
        body.add_widget(main)
        self.root_box.add_widget(body)
        if self.explorer_open and not wide:
            Clock.schedule_once(lambda dt: self._explorer_sheet(), 0)
            self.explorer_open = False

    def _empty_state(self):
        c = column(padding=dp(24), spacing=dp(12))
        c.add_widget(label("No file open", "H6"))
        c.add_widget(label("Open the project tree and pick a file.", "Body2", "muted"))
        c.add_widget(primary_btn("Open project tree", self.toggle_explorer))
        return c

    def _editor_area(self, doc):
        cfg = self.core.config
        wrap = MDBoxLayout(orientation="horizontal")
        self.code = NDXCodeInput(tab_width=cfg.get("editor.tab_width", 4), text=doc.text,
                                 font_size=dp(cfg.get("editor.font_size", 14)))
        lang = self.core.services.get("languages").get(doc.language)
        self.code.set_language(lang.lexer if lang else "text")
        theme = next(iter(self.core.services.get("plugins").themes.values()), None)
        self.code.set_style((theme or {}).get("pygments_style", "monokai" if T.dark else "default"))
        self._loading = True
        self.code.text = doc.text
        self._loading = False
        if cfg.get("editor.line_numbers", True):
            wrap.add_widget(LineGutter(self.code))
        wrap.add_widget(self.code)
        self.code.bind(text=self._on_text, cursor=self._on_cursor)
        self.schedule_diagnostics()
        return wrap

    def _suggestion_bar(self):
        self.sugg = MDBoxLayout(orientation="horizontal", adaptive_height=True, size_hint_y=None, height=dp(34),
                                spacing=dp(6), padding=(dp(6), dp(2)))
        return self.sugg

    def _toolbar(self):
        bar = row(size_hint_y=None, height=dp(48), padding=(dp(6), 0))
        from ...core.paths import is_android
        if is_android():
            bar.add_widget(icon_btn("folder-open-outline", self.import_external_file, "accent"))
        bar.add_widget(icon_btn("content-save-outline", self.save, "accent"))
        if is_android():
            bar.add_widget(icon_btn("content-save-edit-outline", self.save_as_external))
        bar.add_widget(icon_btn("undo", lambda: self.code.do_undo()))
        bar.add_widget(icon_btn("redo", lambda: self.code.do_redo()))
        bar.add_widget(icon_btn("magnify", self.find_dialog))
        bar.add_widget(icon_btn("format-indent-increase", lambda: self.code.do_tab()))
        bar.add_widget(icon_btn("swap-horizontal", lambda: self.app.choose_run_target()))
        bar.add_widget(icon_btn("hammer", self.build_project))
        bar.add_widget(icon_btn("play", self.run_project, "ok"))
        bar.add_widget(icon_btn("alert-circle-outline", self.toggle_problems, "warn" if self.diags else "muted"))
        bar.add_widget(icon_btn("text-box-outline", self.toggle_output))
        return bar

    def _statusbar(self, doc):
        self.status = label("", "Caption", "muted", size_hint_y=None, height=dp(22), padding=(dp(10), 0))
        self._update_status()
        return self.status

    def _problems_panel(self):
        sc = ScrollView(size_hint_y=None, height=dp(120))
        col = column(padding=dp(8), spacing=dp(2))
        if not self.diags:
            col.add_widget(label("No problems found by built-in checks.", "Caption", "ok"))
        for d in self.diags[:50]:
            r = row(size_hint_y=None, height=dp(24))
            r.add_widget(text_btn("%d:%d" % (d.line, d.col), lambda d=d: self.code.goto_line(d.line, max(0, d.col - 1)),
                                  "error" if d.severity == "error" else "warn", size_hint_x=None, width=dp(64)))
            r.add_widget(label(d.message, "Caption", adaptive_height=False, valign="middle"))
            col.add_widget(r)
        sc.add_widget(col)
        return sc

    def _output_panel(self):
        sc = ScrollView(size_hint_y=None, height=dp(150))
        self.out_label = label("".join(self.output_lines[-200:]) or "Run output appears here.", "Caption",
                               font_name="NDXMono", markup=False, padding=(dp(8), dp(4)))
        sc.add_widget(self.out_label)
        return sc

    # ---- tabs & text
    def _sync_text(self):
        d = self.session.current()
        if d is not None and self.code is not None:
            d.text = self.code.text

    def switch(self, i):
        self._sync_text()
        self.session.active = i
        self.session.persist()
        self.build_ui()

    def close_tab(self, i):
        self._sync_text()
        doc = self.session.docs[i]
        if doc.modified:
            confirm_dialog("Unsaved changes", "Close '%s' without saving?" % doc.name,
                           lambda: self._close(i), "Discard", danger=True)
        else:
            self._close(i)

    def _close(self, i):
        self.session.close(i)
        self.code = None
        self.session.persist()
        self.build_ui()

    def _on_text(self, inst, value):
        if self._loading:
            return
        d = self.session.current()
        if d:
            was = d.modified
            d.text = value
            if was != d.modified:
                self.build_ui_tabs_only()
        self.schedule_diagnostics()
        self._update_suggestions()
        if self.core.config.get("editor.autosave", False) and d and d.modified:
            self.save()

    def build_ui_tabs_only(self):
        pass  # modified dot refreshes on the next full rebuild (tab switch / save)

    def _on_cursor(self, *_):
        self._update_status()
        self._update_suggestions()

    def _update_status(self):
        d = self.session.current()
        if not d or not hasattr(self, "status") or not self.code:
            return
        col, row_ = self.code.cursor
        br = self.code.bracket_info()
        lang = self.core.services.get("languages").get(d.language)
        self.status.text = "%s   Ln %d, Col %d%s   %s" % (
            lang.name if lang else "Plain text", row_ + 1, col + 1,
            ("   bracket ↔ %d:%d" % (br[1] + 1, br[0] + 1)) if br else "",
            ("[color=%s]%d problem(s)[/color]" % (T.hex("warn"), len(self.diags))) if self.diags else "")

    def _update_suggestions(self):
        if not hasattr(self, "sugg") or not self.code:
            return
        self.sugg.clear_widgets()
        d = self.session.current()
        lang = self.core.services.get("languages").get(d.language) if d else None
        pre = current_prefix(self.code.text, self.code.cursor_index())
        for w in complete(pre, self.code.text, lang, limit=6):
            self.sugg.add_widget(text_btn(w, lambda w=w, pre=pre: self._apply_completion(w, pre), "text"))

    def _apply_completion(self, word, prefix):
        for _ in prefix:
            self.code.do_backspace(from_undo=True, mode="bkspc")
        self.code.insert_text(word)

    # ---- diagnostics (debounced, off the UI thread)
    def schedule_diagnostics(self):
        if self._diag_ev:
            self._diag_ev.cancel()
        self._diag_ev = Clock.schedule_once(lambda dt: self._run_diag(), 0.8)

    def _run_diag(self):
        d = self.session.current()
        if not d or not self.code:
            return
        text, lang = self.code.text, d.language
        self.core.tasks.submit(diagnose, text, lang, name="diagnostics", on_done=self._diag_done)

    def _diag_done(self, result):
        self.diags = result or []
        self._update_status()

    # ---- actions
    def save(self):
        self._sync_text()
        d = self.session.current()
        if not d:
            return
        try:
            self.session.save(d)
            toast("Saved " + d.name)
            self.build_ui()
        except OSError as e:
            message_dialog("Save failed", str(e))

    def find_dialog(self):
        if not self.code:
            return
        form_dialog("Find & replace", [("find", "Find", ""), ("repl", "Replace with", "")],
                    self._do_find, "Replace all",
                    extra=[("mode", ["Find next", "Replace all"], "Find next")])

    def _do_find(self, v):
        if v["mode"] == "Replace all":
            text, n = EditorSession.replace_all(self.code.text, v["find"], v["repl"])
            self.code.text = text
            toast("%d replacement(s)" % n)
        elif not self.code.find_from_cursor(v["find"]):
            toast("Not found")

    def toggle_explorer(self):
        self.explorer_open = not self.explorer_open
        self._sync_text()
        self.build_ui()

    def toggle_problems(self):
        self.show_problems = not self.show_problems
        self._sync_text()
        self.build_ui()

    def toggle_output(self):
        self.show_output = not self.show_output
        self._sync_text()
        self.build_ui()

    # ---- explorer
    def _tree_rows(self, path, depth, rows):
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return
        for e in entries:
            if e.name in IGNORED:
                continue
            rows.append((e.path, e.name, e.is_dir(), depth))
            if e.is_dir() and e.path in self.expanded:
                self._tree_rows(e.path, depth + 1, rows)

    def _explorer_panel(self, on_pick=None):
        panel = card(size_hint=(None, 1), width=dp(240), padding=dp(6))
        panel.adaptive_height = False
        panel.add_widget(label(self.project.name if self.project else "No project open", "Subtitle2",
                               size_hint_y=None, height=dp(28), padding=(dp(6), 0)))
        sc = ScrollView()
        col = column(spacing=dp(0))
        if not self.project:
            col.add_widget(label("Open a project from the Projects screen.", "Caption", "muted", padding=(dp(6), 0)))
        else:
            rows = []
            self._tree_rows(self.project.path, 0, rows)
            for path, name, is_dir, depth in rows:
                r = NavItem(path, "folder-outline" if is_dir else "file-outline", name,
                            lambda k, d=is_dir, on_pick=on_pick: self._tree_click(k, d, on_pick), compact=False)
                r.orientation = "horizontal"
                r.height = dp(34)
                r.padding = (dp(6 + 14 * depth), 0)
                r.clear_widgets()
                r.add_widget(icon("chevron-down" if (is_dir and path in self.expanded) else
                                  "chevron-right" if is_dir else "file-outline", "muted", 18))
                r.add_widget(label(name, "Body2", adaptive_height=False, valign="middle", shorten=True))
                col.add_widget(r)
        sc.add_widget(col)
        panel.add_widget(sc)
        tools = row(size_hint_y=None, height=dp(40))
        tools.add_widget(icon_btn("file-plus-outline", self.new_file_dialog))
        tools.add_widget(icon_btn("refresh", self.build_ui))
        panel.add_widget(tools)
        return panel

    def _explorer_sheet(self):
        from kivy.uix.modalview import ModalView
        mv = ModalView(size_hint=(.9, .8), background="", background_color=(0, 0, 0, .5))
        panel = self._explorer_panel(on_pick=mv.dismiss)
        panel.size_hint = (1, 1)
        mv.add_widget(panel)
        mv.open()

    def _tree_click(self, path, is_dir, on_pick):
        if is_dir:
            self.expanded ^= {path}
            self.build_ui()
            return
        if on_pick:
            on_pick()
        self.open_file(path)
        self.build_ui()

    def new_file_dialog(self):
        if not self.project:
            return
        form_dialog("New file", [("name", "File name (relative to project)", "")], self._new_file, "Create")

    def _new_file(self, v):
        from ...security import safe_join
        try:
            target = safe_join(self.project.path, v["name"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=False)
            self.open_file(str(target))
            self.build_ui()
        except Exception as e:
            message_dialog("Cannot create file", str(e))

    # ---- build / run
    def _commands(self, configs):
        return [c for c in configs]

    def _exec(self, configs, label_):
        self._sync_text()
        if not self.project:
            message_dialog("No project", "Open a project first (Projects > Open).")
            return
        if not configs:
            d = self.session.current()
            lang = self.core.services.get("languages").get(d.language) if d else None
            tmpl = (lang.run if label_ == "run" else lang.build) if lang else ""
            if not tmpl:
                message_dialog("Nothing to %s" % label_, "This project/file has no %s configuration." % label_)
                return
            configs = [{"name": label_, "command": tmpl}]
        for d in self.session.docs:
            if d.modified:
                self.session.save(d)
        cur = self.session.current()
        if label_ == "run" and cur and cur.path.lower().endswith(".py"):
            try:
                source = Path(cur.path).read_text(encoding="utf-8")
            except Exception:
                source = ""

            from ...python_runtime.embedded import looks_like_kivy
            is_kivy = looks_like_kivy(source) and (".run()" in source or "runTouchApp(" in source)

            if is_kivy:
                try:
                    from ...python_runtime.gui import EmbeddedKivySession
                    if not hasattr(self.app, "gui_session"):
                        self.app.gui_session = EmbeddedKivySession(self.app)

                    self.app.gui_session.start(cur.path, self.project.path, label_)
                except Exception as exc:
                    self.output_lines.append(
                        "[NDX GUI ERROR] %s\n" % exc
                    )
                    self.show_output = True

                self.build_ui()
                return

        target = self.app.run_target
        for cfg in configs[:1]:
            root = "/project" if target.startswith("proot:") else self.project.path
            cur = self.session.current()
            argv = expand_command(cfg["command"], root, cur.path if cur else "")
            self.output_lines.append("$ %s   [%s]\n" % (" ".join(argv), target))
            self.show_output = True
            self.core.services.get("runner").run(argv, self.project.path, target, label_)
        self.build_ui()

    def run_project(self):
        self._exec(self.project.run_configs if self.project else [], "run")

    def build_project(self):
        self._exec(self.project.build_configs if self.project else [], "build")

    def _on_run_output(self, ev):
        self.output_lines.append(ev.payload["text"])
        self.output_lines = self.output_lines[-1000:]
        if self.show_output and hasattr(self, "out_label"):
            self.out_label.text = "".join(self.output_lines[-200:])

    def _on_run_done(self, ev):
        p = ev.payload
        self.output_lines.append("[exit %s]\n" % ("denied" if p.get("denied") else p.get("code")))
        if self.show_output and hasattr(self, "out_label"):
            self.out_label.text = "".join(self.output_lines[-200:])
