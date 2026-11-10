import os

from kivy.metrics import dp

from ...core.errors import NDXError
from ...project_manager import TEMPLATES
from ...utilities import human_size
from ..theme import T
from ..widgets import (ResponsiveGrid, card, column, confirm_dialog, form_dialog, icon, label, message_dialog,
                       primary_btn, row, text_btn, toast, icon_btn)
from .base import NDXScreen


class ProjectsScreen(NDXScreen):
    key = "projects"
    title = "Projects"

    def actions(self):
        items = [["folder-plus-outline", lambda *_: self.new_dialog()],
                 ["folder-download-outline", lambda *_: self.import_dialog()]]
        if self._is_android():
            items.append(["folder-upload-outline", lambda *_: self.export_picker()])
        return items

    @property
    def pm(self):
        return self.core.services.get("projects")

    def render(self, body):
        projects = self.pm.list()
        top = row()
        top.add_widget(primary_btn("New project", self.new_dialog))
        top.add_widget(text_btn("Import folder", self.import_dialog))
        body.add_widget(top)
        if not projects:
            body.add_widget(label("No projects. Create one from a template.", "Body2", "muted"))
        grid = ResponsiveGrid(min_item_dp=320)
        body.add_widget(grid)
        langs = self.core.services.get("languages")
        for p in projects:
            c = card()
            head = row(size_hint_y=None, height=dp(34))
            head.add_widget(icon("folder-outline", "accent", 22))
            head.add_widget(label(p.name, "Subtitle1", adaptive_height=False, valign="middle", shorten=True))
            c.add_widget(head)
            lang = langs.get(p.language)
            git = self.pm.git(p)
            branch = ""
            if git.available() and git.is_repo():
                st = git.status()
                branch = "  [color=%s]git: %s (%d changes)[/color]" % (
                    T.hex("muted"), st.get("branch", "?").split("...")[0], len(st.get("changes", []))) if st.get("ok") else ""
            c.add_widget(label("%s  ·  %s%s" % (lang.name if lang else "Mixed", human_size(self.pm.size(p)), branch),
                               "Caption", "muted"))
            actions = row(size_hint_y=None, height=dp(40))
            actions.add_widget(primary_btn("Open", lambda p=p: self.app.open_project(p)))
            if self._is_android():
                actions.add_widget(icon_btn("folder-upload-outline", lambda p=p: self.export_picker(p)))
            actions.add_widget(icon_btn("pencil-outline", lambda p=p: self.rename(p)))
            actions.add_widget(icon_btn("source-branch", lambda p=p: self.git_menu(p)))
            actions.add_widget(icon_btn("delete-outline", lambda p=p: self.delete(p), "error"))
            c.add_widget(actions)
            grid.add_widget(c)

    # ---- dialogs
    def new_dialog(self):
        names = ["%s (%s)" % (t["name"], tid) for tid, t in TEMPLATES.items()]
        form_dialog("New project", [("name", "Project name", "")], self._create, "Create",
                    extra=[("tpl", names, names[0])])

    def _create(self, v):
        tid = v["tpl"].rsplit("(", 1)[1].rstrip(")")
        try:
            p = self.pm.create(v["name"], tid)
        except NDXError as e:
            message_dialog("Cannot create project", str(e))
            return
        toast("Created " + p.name)
        self.rebuild()

    def import_dialog(self):
        if self._is_android():
            try:
                self.app.get_saf().open_tree(self._on_import_tree)
            except Exception as e:
                message_dialog("Cannot open Android folder picker", str(e))
            return
        form_dialog("Import folder", [("path", "Absolute folder path", "")], self._import, "Import")

    @staticmethod
    def _is_android():
        from ...core.paths import is_android
        return is_android()

    def _on_import_tree(self, result):
        if not result.get("ok"):
            if not result.get("cancelled"):
                message_dialog("Import failed", result.get("error", "Could not select folder"))
            return
        import re, uuid
        from pathlib import Path
        meta = self.core.paths.projects
        name = "Imported-" + uuid.uuid4().hex[:6]
        target = meta / name
        try:
            saf = self.app.get_saf()
            # Use provider metadata for a friendly project directory name.
            try:
                info = saf.query_document(result["uri"])
                candidate = (info.get("name") or "Imported project").strip()
                candidate = re.sub(r"[^A-Za-z0-9 ._-]+", "_", candidate).strip(" .")
                if candidate:
                    name = candidate[:48]
                    target = meta / name
                    suffix = 2
                    while target.exists():
                        target = meta / (name + " " + str(suffix)); suffix += 1
            except Exception:
                pass
            target.mkdir(parents=True, exist_ok=False)
            saf.copy_tree_to_private(result["uri"], target)
            project = self.pm.import_folder(str(target))
            toast("Imported " + project.name)
            self.rebuild()
        except Exception as e:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            message_dialog("Cannot import folder", str(e))

    def export_picker(self, project=None):
        project = project or getattr(self.app.screens.get("editor"), "project", None)
        if project is None:
            message_dialog("No project selected", "Open or select a project before exporting.")
            return
        try:
            self.app.get_saf().open_tree(lambda result, p=project: self._on_export_tree(result, p))
        except Exception as e:
            message_dialog("Cannot open folder picker", str(e))

    def _on_export_tree(self, result, project):
        if not result.get("ok"):
            return
        try:
            count = self.app.get_saf().export_directory_to_tree(
                result["uri"], project.path
            )
            toast("Exported %s (%d items)" % (project.name, count))
        except Exception as e:
            message_dialog("Cannot export project", str(e))

    def _import(self, v):
        try:
            self.pm.import_folder(v["path"])
            self.rebuild()
        except (NDXError, OSError) as e:
            message_dialog("Cannot import", str(e))

    def rename(self, p):
        form_dialog("Rename project", [("name", "New name", p.name)], lambda v: self._rename(p, v["name"]), "Rename")

    def _rename(self, p, name):
        try:
            self.pm.rename(p, name)
            self.rebuild()
        except (NDXError, OSError) as e:
            message_dialog("Cannot rename", str(e))

    def delete(self, p):
        confirm_dialog("Delete project?", "'%s' and ALL its files will be permanently deleted." % p.name,
                       lambda: (self.pm.delete(p, confirmed=True), self.rebuild()), "Delete", danger=True)

    def git_menu(self, p):
        git = self.pm.git(p)
        if not git.available():
            message_dialog("Git not available", "No git executable was found. Install git inside a Linux environment.")
            return
        if not git.is_repo():
            confirm_dialog("Initialize repository?", "Run 'git init' in %s?" % p.name,
                           lambda: (git.init(), self.rebuild()), "git init")
            return
        st = git.status()
        log = "\n".join(git.log(5)) or "(no commits)"
        message_dialog("Git: " + p.name, "Branch: %s\nChanges: %d\n\nRecent commits:\n%s" % (
            st.get("branch"), len(st.get("changes", [])), log))
