# Compatibility matrix
Legend: **T** automated tests here (Linux, mock executables, no internet) · **S** stub smoke only · **U** untested on Android · **X** unsupported by Android restrictions.

| Feature | Desktop/Linux | Android |
|---|---|---|
| Core, events, config, tasks, permissions | T | U |
| Projects, editor model, languages | T | U |
| Toolchain Manager (install/verify/rollback/resolve/CLI) | T (mock archives) | U; real upstream URLs/checksums unverified |
| PRoot auto-install (fake proot script) | T | U |
| Linux env install/check/path security | T | U (PRoot behaviour on devices unknown) |
| Terminal Bridge: host / linux | T | U |
| Terminal Bridge: termux | T (fake transport) | U (never run) |
| Python targets: embedded (subprocess + in-process), venv, linux, termux routing | T | U (in-process on Android unverified) |
| Git extension | T | U |
| IPC security, bindings (py/js/lua) | T (py) / js, lua untested | U |
| Supervisor / background link | T | U (not run as an Android service) |
| ADB states (fake adb) | T | U; pairing flow untested |
| Resource admission/tracking | T | U |
| Kivy/KivyMD UI incl. new buttons | S | U |
| Build config / requirements split | T (static) | APK **not built** |
| Running arbitrary ELF binaries from app-private storage on Android 10+ | – | X for targetSdk >= 29 unless shipped as native libs; NDX uses PRoot/native-lib/Termux paths instead |
| Reading Termux private files | – | X |
Target devices: Android 7.0+ (API 24+), ARM64 primary, ARMv7.
