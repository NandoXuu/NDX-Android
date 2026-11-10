# Known limitations (honest list)
**Not verified in the build environment:** the APK build, running on any Android device/emulator, real Kivy/KivyMD
rendering, soft-keyboard behavior in the terminal "live" mode, density/size/theme appearance, PRoot installs, adb flows,
llama-server runs, GitHub network calls. Verified: 268 automated tests on Linux (mock executables, no internet) and a stubbed
UI smoke test that exercises NDX's own UI code (not Kivy itself). See IMPLEMENTATION_REPORT.md.
**Design limits:**
* No compilers/shells beyond Android's `/system/bin/sh` unless you install a Linux environment (NDX installs PRoot for you; untested on devices).
* Terminal: VT emulation is a subset of xterm (no mouse reporting, no bracketed paste, limited charsets); Kivy markup has no
  background colors, so reverse video is drawn as a highlight color.
* Editor: no code folding; LSP client exists but is not connected to the editor UI; completion is keyword/buffer based.
* Debugger: only reports debugger availability; no debug UI. Git: CLI (`ndx git`) and API only; no git UI screen.
* Servers: external processes use `RLIMIT_AS` (address space, generous x4) and a polling watcher; NOT hard RAM limits.
  Built-in servers live in the app process, which Android may kill. External-process servers can be delegated to the
  foreground-service supervisor (BACKGROUND.md); this reduces but never removes the chance of being stopped. The built-in static server's RSS is app-wide.
* AI: llama.cpp only; ONNX Runtime not integrated; RAM numbers are estimates; no GPU memory display.
* GUI compatibility: status matrix only; no display bridge launcher.
* Application Framework: only the headless backend; no native Android/GTK/Qt backend; JS binding untested.
* Plugins: capability checks are not an OS sandbox; package signatures are not verified.
* Monochrome themed icon is shipped but not wired into the manifest (ASSETS.md).
* Theme switch recolors most UI live; a few widgets fully refresh on next screen entry.
* Toolchain catalog URLs/versions and upstream checksums were NOT verified (no network when written). Unpinned downloads need
  explicit consent (TOOLCHAIN.md). No native `proot`/`adb` binary is bundled in the APK.
* Termux: `AndroidIntentTransport` (jnius + BroadcastReceiver), RUN_COMMAND, package visibility (`<queries>`) never run on a device.
* PRoot on real devices (seccomp/Android 14+ limits, performance), ADB pairing flows and foreground-service behaviour: untested.
* Embedded Python: same process as NDX, one script at a time, uncancellable in native code, no pip (PYTHON.md).
* `SecretStore` is a 0600 file: not encrypted, not hardware-backed (no Android Keystore integration).
* API 34+ foreground-service types are documented, not implemented. UI additions (Install PRoot, Check Linux Environment,
  ADB state, Termux card) were only exercised by the stub smoke test, not rendered.
