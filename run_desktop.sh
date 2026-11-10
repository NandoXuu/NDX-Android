#!/usr/bin/env bash
# Run NDX on a desktop with Kivy/KivyMD installed (pip install -r requirements.txt) for UI development.
cd "$(dirname "$0")" && NDX_HOME="${NDX_HOME:-$HOME/.ndx}" python3 main.py
