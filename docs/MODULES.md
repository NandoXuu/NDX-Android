# Module reference (implemented behavior only)

| Module (`ndx.`) | Interface | What works |
|---|---|---|
| `core` | `NDXCore`, `EventBus.subscribe/publish`, `ConfigManager.get/set`, `TaskManager.submit`, `ConsentGateway.request` | tested |
| `project_manager` | `ProjectManager.create/list/open/rename/delete/save_config/import_folder/recent/git` | 11 templates, name validation, delete needs `confirmed=True` and stays inside the projects dir |
| `language_support` | `LanguageRegistry.detect/toolchain_status`, `complete()`, `diagnose()`, `LspClient` | 15 languages; diagnostics in-process for Python/JSON/TOML, bracket check for brace languages, external `sh -n`/`luac -p` when present; LSP client starts a server only if installed |
| `compiler_manager` | `RunService.run(argv, cwd, target_id)` | no-shell argv execution, streamed output events, consent per run, `HostTarget`, `ProotTarget` |
| `terminal` | `PtySession`, `VTScreen`, `ConsoleSession`, `encode_key`, `parse_combo` | real PTY (fork+setsid+TIOCSCTTY, resize/SIGWINCH), VT100/xterm subset incl. alt screen/scroll regions/256 & truecolor |
| `shell_environment` | `ShellEnvironment.profiles/default_profile`, `oh_my_zsh_plan()` | detects bash/zsh/fish/sh/ash/busybox and 20 utilities; Oh My Zsh is a *plan only*, never auto-run |
| `package_manager` | `PackageManager.install_path/install_github/update/uninstall/rollback/verify/search`, `NdxCli` | manifest validation, zip-slip/symlink defense, size limits, checksum, consent, dependency check, install log, rollback, tamper detection |
| `plugin_system` | `PluginManager`, `PluginBase`, `PluginContext` | declarative plugins (no code), code plugins gated by approval + setting + capability checks |
| `linux_manager` | `LinuxManager.install/list/profile/remove/backup/restore/package_command` | rootfs download/extract (safe), env vars, backup; PRoot installed by the Toolchain Manager; `check_environment`; env-id path security |
| `server_manager` | `ServerManager.add/start_server/stop_server/restart/stats` | built-in static HTTP server (gate, counters) + external processes (RLIMIT_AS, nice, policy watcher), loopback default |
| `resource_manager` | `ResourceMonitor.snapshot`, `ResourceManager.reserve`, `RequestGate` | every metric tagged exact/estimate/unavailable |
| `ai_runtime` | `AIRuntimeManager.register/load/unload` | llama.cpp `llama-server` only (must be installed); RAM is an estimate |
| `android_toolkit` | `AdbClient`, `LogcatMonitor`, `CrashLogStore`, `explorer()`, `device_info()` | adb wrapper (adb resolved/installed via Toolchain Manager; states: adb_unavailable/device_disconnected/authorization_required/connected), capability table |
| `gui_compatibility` | `TOOLKITS`, `LinuxGuiBridge.status()` | status matrix only; bridge launch NOT implemented |
| `application_framework` | `Node` widgets, `HeadlessBackend`, `PlatformServices`, `IpcServer` | toolkit-independent tree + IPC + Python/JS bindings; only the headless backend exists |
| `debugger` | `debugger_status(language)` | reports which debugger a language lists; no debug UI |
| `toolchain_manager` | `ToolchainManager.install/remove/update/resolve/doctor`, `ToolRunner.run`, `ToolCli` | transactional installs, resolver, profiles; see TOOLCHAIN.md |
| `termux_integration` | `TermuxIntegration.get_status/run` | optional RUN_COMMAND bridge; device-untested (TERMUX.md) |
| `terminal.bridge` | `TerminalBridge.run/open` | 5 backends (TERMINAL_BRIDGE.md) |
| `python_runtime` | `PythonRuntime.targets/select/run_script/create_venv` | 5 targets (PYTHON.md) |
| `integrations.git` | `GitClient` | real git, user identity, secure auth (GIT.md) |
| `security.secrets` | `SecretStore` | 0600 file, not encrypted |
| `server_manager.supervisor` | `Supervisor`, `BackgroundLink` | service-side supervisor (BACKGROUND.md) |
| `tools` | `run`, `resolve`, `list` | script-to-tool API |
