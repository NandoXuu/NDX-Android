# Changelog

## 1.1.0 - remediation (unreleased, not device-tested)
### Added
- Expanded Android SAF bridge: persisted URI grants, document metadata, import/export helpers, tree listing/creation/deletion, and safer ContentResolver streaming. Updated `docs/ANDROID_SAF.md`; no broad storage permissions added.
- Toolchain Manager wired into the Core: transactional install/update/remove/rollback, resolver with configurable priority and per-runtime PATHs, profiles, `ndx tool`, `ndx run`, `ndx.tools.run` (ToolRunner with consent).
- Automatic PRoot install (`ndx tool install proot`, `ndx linux install --install-proot`, UI button); backend-first rule for Linux installs; `Check Linux Environment` (UI + `ndx linux check`).
- Terminal Bridge with 5 backends reusing the existing PTY; optional Termux integration (state machine, probe, shared-storage rule).
- Python runtime with embedded / external / venv / Termux / Linux targets and RunService fallback.
- Git extension (user-configured identity, secure auth, redaction) and SecretStore.
- IPC hardening: loopback, tokens with scopes, rate limits, per-launch revocable tokens; Python/JS/Lua bindings; docs/IPC.md.
- Service supervisor + BackgroundLink with honest status; `ndx service`.
- Resource Manager: `track`, `check_storage`, `admit` used by installs, processes, servers, AI load advice.
- ADB honest states and sidecar/TOFU checksum handling.
- Requirements split (`requirements/`), build-config tests, docs (TOOLCHAIN, TERMUX, TERMINAL_BRIDGE, PYTHON, BACKGROUND, GIT, compatibility matrix, implementation report).
### Changed
- `LinuxManager.install` refuses to proceed without a runnable backend unless `require_backend=False` (existing test adapted, assertions kept).
- Env ids and rootfs paths validated and symlink-safe.
- buildozer.spec: Termux RUN_COMMAND permission, requirements comment; KivyMD stays 1.2.0.
- UI texts no longer tell the user to copy binaries.
### Fixed
- `ndx linux check` exit code on FAIL; URL validator for local absolute paths; Android backend reason string; ToolCli prints errors instead of tracebacks.
### Tests
- 66 original tests kept; 268 total passing.


## SAF UI integration

- Connected Android folder import to the Projects screen using the system folder picker.
- Added recursive import/export of project directories through `ContentResolver`.
- Added per-project export actions.
- Connected Editor external-file import and Save as to the native SAF pickers.
- Kept editor and project working copies in app-private storage; desktop SAF initialization remains lazy.
