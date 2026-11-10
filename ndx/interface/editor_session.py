"""Editor model (no Kivy): open documents, active tab, session save/restore."""
import os
from dataclasses import dataclass

from ..storage import read_text, atomic_write


@dataclass
class Doc:
    path: str
    text: str
    saved_text: str
    language: str = ""

    @property
    def modified(self):
        return self.text != self.saved_text

    @property
    def name(self):
        return os.path.basename(self.path)


class EditorSession:
    def __init__(self, config, languages):
        self.config, self.languages = config, languages
        self.docs = []
        self.active = -1

    def open(self, path):
        path = os.path.abspath(path)
        for i, d in enumerate(self.docs):
            if d.path == path:
                self.active = i
                return d
        text = read_text(path)
        lang = self.languages.detect(path)
        d = Doc(path, text, text, lang.id if lang else "")
        self.docs.append(d)
        self.active = len(self.docs) - 1
        return d

    def current(self):
        return self.docs[self.active] if 0 <= self.active < len(self.docs) else None

    def save(self, doc=None):
        doc = doc or self.current()
        if doc:
            atomic_write(doc.path, doc.text)
            doc.saved_text = doc.text
        return doc

    def close(self, index):
        if 0 <= index < len(self.docs):
            self.docs.pop(index)
            self.active = min(self.active, len(self.docs) - 1)

    def persist(self):
        self.config.set("session.open_files", [d.path for d in self.docs], save=False)
        cur = self.current()
        self.config.set("session.active_file", cur.path if cur else "")

    def restore(self):
        for p in self.config.get("session.open_files", []):
            try:
                if os.path.isfile(p):
                    self.open(p)
            except (OSError, ValueError):
                continue
        act = self.config.get("session.active_file", "")
        for i, d in enumerate(self.docs):
            if d.path == act:
                self.active = i

    @staticmethod
    def replace_all(text, find, repl):
        if not find:
            return text, 0
        return text.replace(find, repl), text.count(find)
