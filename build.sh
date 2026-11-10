#!/usr/bin/env bash
# Build the NDX debug APK. Run on Linux (or WSL2/Docker) with buildozer prerequisites installed.
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/generate_assets.py          # regenerate icons/splash/resources + buildozer.spec resource list
python3 -m unittest discover -s tests -t .  # core tests must pass before packaging
python3 tools/ui_smoke_test.py
buildozer -v android debug
python3 tools/verify_apk_resources.py bin/*.apk
