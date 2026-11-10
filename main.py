"""NDX entry point (Kivy/KivyMD). Packaged by Buildozer as the app's main.py."""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _install_crash_hook():
    def hook(exc_type, exc, tb):
        try:
            from ndx.core.paths import detect_base_dir
            logs = detect_base_dir() / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with open(logs / "crash.log", "a", encoding="utf-8") as fh:
                fh.write("".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
        finally:
            sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = hook


def main():
    _install_crash_hook()
    from ndx.interface.app import NDXApp
    NDXApp().run()


if __name__ == "__main__":
    main()
