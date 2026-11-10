"""GUI toolkit compatibility. Statuses describe what is TESTED, not hoped for.
"untested" means no verification was done on a physical Android ARM64 device."""
from ..toolchain_manager.manager import host_which as which

# status: untested | experimental | unsupported | planned
TOOLKITS = [
    {"id": "sdl", "name": "SDL2/SDL3", "approach": "B: Android host backend", "arm64": "likely",
     "status": "planned", "requires": "SDL Android activity / python-for-android sdl2 bootstrap (already used by Kivy)",
     "note": "Kivy itself runs on SDL2 on Android; exposing SDL to user projects is not implemented."},
    {"id": "imgui", "name": "Dear ImGui", "approach": "B: Android host backend (SDL2/GLES3 backend)", "arm64": "yes",
     "status": "planned", "requires": "C++ toolchain + imgui sources + SDL2/GLES backend",
     "note": "Official Android example exists upstream; no NDX integration yet."},
    {"id": "iup", "name": "IUP", "approach": "A: Linux GUI bridge", "arm64": "yes (GTK/Motif driver)",
     "status": "untested", "requires": "Linux env + GTK3 + X server bridge", "note": ""},
    {"id": "gtk", "name": "GTK 3/4", "approach": "A: Linux GUI bridge", "arm64": "yes",
     "status": "untested", "requires": "Linux env + X11/Wayland bridge (VNC/X forwarding)", "note": "Slow without GPU."},
    {"id": "qt", "name": "Qt Widgets", "approach": "A: Linux GUI bridge", "arm64": "yes",
     "status": "untested", "requires": "Linux env + qt packages + X bridge", "note": ""},
    {"id": "qml", "name": "Qt / QML", "approach": "A: Linux GUI bridge", "arm64": "yes",
     "status": "untested", "requires": "Linux env + qt-qml + software rendering", "note": "Native Android Qt needs the Qt toolchain."},
    {"id": "wx", "name": "wxWidgets", "approach": "A: Linux GUI bridge", "arm64": "yes (GTK port)",
     "status": "untested", "requires": "Linux env + libwxgtk + X bridge", "note": ""},
]


def toolkit_status():
    return TOOLKITS


class LinuxGuiBridge:
    """Detects whether a display-forwarding component exists. Starting the bridge
    is NOT implemented: this reports prerequisites only."""
    CANDIDATES = ["Xvnc", "vncserver", "Xvfb", "weston", "x11vnc"]

    def __init__(self, bin_dir):
        self.bin_dir = bin_dir

    def prerequisites(self):
        return {c: which(c, [self.bin_dir]) for c in self.CANDIDATES}

    def status(self):
        found = [k for k, v in self.prerequisites().items() if v]
        return {"implemented": False,
                "detail": "Display bridge launch is not implemented. Components found on the host: %s" %
                          (", ".join(found) or "none") + ". X servers normally live inside the Linux environment."}
