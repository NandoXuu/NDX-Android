# NDX - Universal Developer Environment (Python + Kivy/KivyMD edition)

A modular developer workstation for Android: editor, real PTY terminal, package/plugin manager, Linux (PRoot) environments,
local servers with resource budgets, AI runtime management, Android device toolkit. Python orchestrates; existing tools
(shells, compilers, proot, llama.cpp, adb, git) do the specialised work. NDX is **not** a game engine and shares no code with LuaStudio.

* UI: Kivy 2.3 + KivyMD 1.2.0, dark-first, responsive (navigation rail >= 720dp, bottom bar + "More" on phones),
  Material Design icons from KivyMD's bundled icon font.
* Android resources: mipmap-mdpi..xxxhdpi, adaptive + monochrome icon layers, splash, notification icons, values (light/night) - ASSETS.md.
* Honest status and limits: **docs/KNOWN_LIMITATIONS.md** and COMPATIBILITY.md. Nothing is presented as working unless it is
  implemented; unavailable features are labelled in the UI.

```bash
./run_desktop.sh            # desktop UI (pip install -r requirements.txt)
python -m unittest discover -s tests -t .
./build.sh                  # assets + tests + buildozer android debug + APK resource check
```
Android: `android/extra_manifest.xml` declares Termux package visibility; `ndx.android_storage.AndroidSAF`
provides asynchronous document open/create/folder selection through Android's system picker. No broad storage
permission is requested. See `docs/ANDROID_SAF.md`. GitHub Actions builds and uploads a debug APK artifact.

Docs: INSTALL, BUILD, TOOLCHAIN, TERMUX, TERMINAL_BRIDGE, PYTHON, BACKGROUND, GIT, IPC, IMPLEMENTATION_REPORT, CHANGELOG, ARCHITECTURE, MODULES, API, PLUGINS, LANGUAGES, LINUX, PERMISSIONS, ASSETS, TROUBLESHOOTING,
KNOWN_LIMITATIONS, COMPATIBILITY (all in `docs/`). Plugin examples: `plugins/examples`. License: MIT.
