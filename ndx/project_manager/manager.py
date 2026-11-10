"""Project creation, opening, renaming, deletion, per-project configs."""
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from ..core.errors import ValidationError, NDXError
from ..core.registry import Module
from ..integrations.git import GitClient
from ..storage import atomic_write, dir_size
from .templates import TEMPLATES

META_DIR = ".ndx"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


@dataclass
class Project:
    id: str
    name: str
    path: str
    language: str = ""
    created: float = 0.0
    build_configs: list = field(default_factory=list)
    run_configs: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)


class ProjectManager(Module):
    name = "projects"
    title = "Project Manager"

    def __init__(self, projects_dir, data_dir, bus=None, audit=None):
        self.root = Path(projects_dir)
        self.recent_file = Path(data_dir) / "recent_projects.json"
        self.bus = bus
        self.audit = audit or (lambda *a, **k: None)
        self.git_services = {}      # injected by the Core: toolchain / config / secrets / audit / runner

    def start(self, core):
        self.root.mkdir(parents=True, exist_ok=True)
        self.status = "running"

    # -- helpers
    def _emit(self, name, project):
        if self.bus:
            self.bus.publish(name, {"id": project.id, "name": project.name, "path": project.path}, "projects")

    @staticmethod
    def _meta(path):
        return Path(path) / META_DIR / "project.json"

    def _save(self, p):
        atomic_write(self._meta(p.path), json.dumps(asdict(p), indent=2))

    def _load(self, path):
        with open(self._meta(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["path"] = str(path)   # follow the folder if it was moved
        known = {k: v for k, v in data.items() if k in Project.__dataclass_fields__}
        return Project(**known)

    # -- operations
    def create(self, name, template_id="empty"):
        name = name.strip()
        if not NAME_RE.match(name):
            raise ValidationError("Invalid project name (letters, digits, space . _ - ; max 64)")
        tpl = TEMPLATES.get(template_id)
        if not tpl:
            raise ValidationError("Unknown template: %s" % template_id)
        path = self.root / name
        if path.exists():
            raise ValidationError("A project named '%s' already exists" % name)
        path.mkdir(parents=True)
        try:
            for rel, content in tpl["files"].items():
                f = path / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(content, encoding="utf-8")
            p = Project(uuid.uuid4().hex[:12], name, str(path), tpl["language"], time.time(),
                        list(tpl["build"]), list(tpl["run"]))
            self._save(p)
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        self.touch_recent(p)
        self._emit("project.created", p)
        return p

    def import_folder(self, path):
        path = Path(path)
        if not path.is_dir():
            raise ValidationError("Not a directory: %s" % path)
        if self._meta(path).exists():
            return self._load(path)
        p = Project(uuid.uuid4().hex[:12], path.name, str(path), "", time.time())
        self._save(p)
        return p

    def list(self):
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and self._meta(d).exists():
                try:
                    out.append(self._load(d))
                except (OSError, ValueError, TypeError):
                    continue
        return out

    def open(self, path):
        p = self._load(path)
        self.touch_recent(p)
        self._emit("project.opened", p)
        return p

    def save_config(self, project):
        self._save(project)
        self._emit("project.saved", project)

    def rename(self, project, new_name):
        new_name = new_name.strip()
        if not NAME_RE.match(new_name):
            raise ValidationError("Invalid project name")
        new_path = Path(project.path).with_name(new_name)
        if new_path.exists():
            raise ValidationError("Target name already exists")
        os.rename(project.path, new_path)
        project.name, project.path = new_name, str(new_path)
        self._save(project)
        self._emit("project.renamed", project)
        return project

    def delete(self, project, confirmed=False):
        if not confirmed:
            raise NDXError("Deletion requires explicit confirmation")
        path = Path(project.path).resolve()
        if self.root.resolve() not in path.parents:
            raise ValidationError("Refusing to delete a folder outside the projects directory")
        shutil.rmtree(path)
        self.audit("project.delete", name=project.name, path=str(path))
        self._emit("project.deleted", project)

    def size(self, project):
        return dir_size(project.path)

    def git(self, project):
        return GitClient(project.path, **self.git_services)

    # -- recents
    def recent(self, limit=8):
        try:
            with open(self.recent_file, "r", encoding="utf-8") as fh:
                paths = json.load(fh)
        except (OSError, ValueError):
            return []
        out = []
        for pth in paths:
            if self._meta(pth).exists():
                try:
                    out.append(self._load(pth))
                except Exception:
                    pass
        return out[:limit]

    def touch_recent(self, project):
        try:
            with open(self.recent_file, "r", encoding="utf-8") as fh:
                paths = json.load(fh)
        except (OSError, ValueError):
            paths = []
        paths = [project.path] + [p for p in paths if p != project.path]
        atomic_write(self.recent_file, json.dumps(paths[:20]))
