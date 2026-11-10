# Installation

## Desktop development (UI + core)
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt     # = requirements/desktop.txt
./run_desktop.sh              # NDX_HOME defaults to ~/.ndx
python -m pytest -q   # or: python -m unittest discover -s tests -t .
python tools/ui_smoke_test.py
```
The core (everything outside `ndx/interface`) needs only Python 3.11+ and `packaging` (+ optional `psutil`, `certifi`).

## Android
Build the APK (see BUILD.md) and install it: `adb install -r bin/ndx-*-debug.apk`, or copy it to the phone and open it
(enable "install unknown apps" for your file manager). Minimum Android 7.0 (API 24); ARM64 and ARMv7.

First run creates its private data directory (`<app-private>/ndx_home`): `projects/ plugins/ environments/ data/ logs/ bin/ models/ cache/`.
Nothing is written to shared storage; no storage permission is requested.

## Tools after installation
Nothing needs to be copied into `bin/`. Use the Environments screen or:
```
ndx tool profile list          # groups of tools
ndx tool install proot         # Linux environments
ndx tool install git
ndx linux install alpine --install-proot
ndx tool doctor                # validate everything installed
```
Requirement sets: `requirements/desktop.txt`, `android.txt` (APK), `optional.txt`, `downloadable.md`.
