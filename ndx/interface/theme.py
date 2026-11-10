"""Design tokens (dark-first). Colors are plain hex; helpers convert for Kivy."""
from kivy.utils import get_color_from_hex

DARK = {"bg": "#0D1117", "surface": "#161B22", "surface2": "#1C2330", "border": "#30363D",
        "text": "#E6EDF3", "muted": "#8B949E", "error": "#F85149", "ok": "#3FB950", "warn": "#D29922",
        "on_accent": "#041311"}
LIGHT = {"bg": "#F6F8FA", "surface": "#FFFFFF", "surface2": "#EEF1F4", "border": "#D0D7DE",
         "text": "#1F2328", "muted": "#59636E", "error": "#CF222E", "ok": "#1A7F37", "warn": "#9A6700",
         "on_accent": "#FFFFFF"}
ACCENTS = {"Teal": ("#2DD4BF", "#14968C"), "Blue": ("#58A6FF", "#0969DA"), "Purple": ("#A371F7", "#8250DF"),
           "Orange": ("#F0883E", "#BC4C00"), "Pink": ("#DB61A2", "#BF3989")}

# Material Design icon names (KivyMD ships the Material Design Icons font)
NAV = [("home", "home-outline", "Home"), ("projects", "folder-multiple-outline", "Projects"),
       ("editor", "code-braces", "Editor"), ("terminal", "console", "Terminal"),
       ("packages", "package-variant-closed", "Packages"), ("environments", "layers-outline", "Environments"),
       ("servers", "server-network", "Servers"), ("toolkit", "cellphone-cog", "Device Toolkit"),
       ("settings", "cog-outline", "Settings")]


class Theme:
    def __init__(self):
        self.dark = True
        self.accent_name = "Teal"

    def configure(self, dark, accent):
        self.dark = bool(dark)
        self.accent_name = accent if accent in ACCENTS else "Teal"

    @property
    def tokens(self):
        return DARK if self.dark else LIGHT

    def hex(self, name):
        if name == "accent":
            return ACCENTS[self.accent_name][0 if self.dark else 1]
        return self.tokens[name]

    def c(self, name, alpha=None):
        col = get_color_from_hex(self.hex(name))
        if alpha is not None:
            col[3] = alpha
        return col

    def status_color(self, state):
        return {"running": "ok", "ready": "ok", "Available": "ok", "ok": "ok", "exact": "ok",
                "failed": "error", "error": "error", "Unsupported": "error",
                "unavailable": "warn", "Restricted": "warn", "Permission Required": "warn",
                "estimate": "warn", "starting": "warn"}.get(state, "muted")


T = Theme()
