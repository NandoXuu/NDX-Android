# NDX remediation - implementation report

Environment of the work: Linux container, Python 3.13, **no network, no Android SDK/NDK, no Kivy runtime, no device**.
Result of the last run: `python -m pytest -q` -> **268 passed**; `python tools/ui_smoke_test.py` -> UI SMOKE OK (stub widgets).
**The APK was not built. Nothing here was run on Android.**

## Spec coverage (sections 1-20)
| § | Item | Implemented | Tested (Linux, mocks) | Partially implemented | Not tested on Android | Unsupported by Android |
|---|---|---|---|---|---|---|
| 1 | Audit of existing project | yes (existing Toolchain Manager found unwired; wired) | - | | | |
| 2 | Toolchain Manager (install/validate/update/remove, shims, rollback) | yes | yes (test_toolchain, 29) | catalog URLs/checksums unverified | yes | |
| 3 | Automatic PRoot install | yes (native_lib > download > termux > manual) | yes with fake proot script | no real proot binary bundled/verified | yes | exec of downloaded ELF from app storage on targetSdk>=29 may be blocked |
| 4 | `ndx tool` CLI + profiles | yes | yes | | | |
| 5 | Resolver, configurable priority, per-runtime PATH | yes | yes | | | |
| 6 | Terminal Bridge, 5 backends, reuses PTY | yes | yes (test_bridge, 32) | termux has no PTY | yes | |
| 7 | `ndx.tools.run`, argv arrays, consent | yes | yes | | | |
| 8 | Optional Termux integration | yes | state machine/fake transport only | real intent transport written, never run | yes | reading Termux private storage |
| 9 | Linux Manager integration + Check Linux Environment | yes | yes (25) | | PRoot on devices | |
| 10 | Env-id path security | yes | yes | | | |
| 11 | Git extension | yes | yes (33) | no UI screen; SecretStore not encrypted | yes | |
| 12 | Python execution targets | yes | yes (15) | in-process embedded limits (PYTHON.md) | in-process on Android | |
| 13 | Foreground service / supervisor honesty | yes | yes (8; desktop subprocess) | no live notification-text update; API 34+ types only documented | yes | guaranteed persistence |
| 14 | IPC security | yes | yes (30) | JS/Lua bindings untested | yes | |
| 15 | ADB states | yes | yes (fake adb) | | pairing on device | |
| 16 | buildozer audit | yes (spec, split requirements, static tests) | static only (13) | `<queries>` for Termux not added | APK not built | |
| 17 | Resource Manager integration | yes (track/check_storage/admit; installs, processes, servers, AI advice) | yes | AI load only exposes `admission()` advice | | |
| 18 | Tests (mock executables, no internet) | yes, 268 | yes | UI only via stubs | | |
| 19 | Implementation rules | followed: no mockups, no feature/test removal | | | | |
| 20 | Final ZIP | produced | | | | |

## Things changed that you should know about
* One pre-existing Linux install test needed `require_backend=False` after the new backend-first rule; assertions were kept.
* `RunService` gained an `inline` target kind (embedded Python).
* Unverified downloads require explicit consent and record a hash (trust on first use).

## Honest list of what is NOT proven
* Any behaviour on a real Android device or emulator; APK build; Kivy/KivyMD rendering of new buttons.
* Upstream URLs, versions and checksums in the built-in catalog.
* Termux RUN_COMMAND, PRoot on Android 10-14, adb wireless pairing, foreground-service survival, embedded in-process Python on Android.
* A tool is only ever reported "installed" after an executable probe succeeded - verified against mock executables only.
