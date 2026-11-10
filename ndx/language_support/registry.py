import os
from ..toolchain_manager.manager import host_which as which


class LanguageRegistry:
    def __init__(self, extra_bin_dirs=()):
        self._langs = {}
        self.extra_bin_dirs = list(extra_bin_dirs)

    def register(self, lang):
        self._langs[lang.id] = lang

    def unregister_source(self, source):
        for k in [k for k, v in self._langs.items() if v.source == source]:
            del self._langs[k]

    def get(self, lang_id):
        return self._langs.get(lang_id)

    def all(self):
        return sorted(self._langs.values(), key=lambda l: l.name.lower())

    def detect(self, path):
        ext = os.path.splitext(str(path))[1].lower()
        base = os.path.basename(str(path))
        # plugin-provided definitions take precedence over built-ins
        for l in sorted(self._langs.values(), key=lambda l: l.source == "builtin"):
            if ext in l.extensions or base in l.extensions:
                return l
        return None

    def toolchain_status(self, lang):
        """Map tool -> resolved path or None (None = not installed here)."""
        return {t: which(t, self.extra_bin_dirs) for t in lang.tools}

    def is_ready(self, lang):
        st = self.toolchain_status(lang)
        return bool(st) and all(st.values())
