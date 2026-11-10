# Architecture

```
main.py ─> ndx.interface.app.NDXApp (Kivy/KivyMD only here)
                │ uses
                ▼
        ndx.core.NDXCore  (no UI dependency)
   EventBus · ConfigManager · LoggingService(+audit) · TaskManager · ServiceRegistry · ModuleRegistry · ConsentGateway
                │ registers modules (each isolated: a failing module is marked `failed`, the app keeps running)
   projects · runner · shell · terminal · packages · console · servers · linux · ai · plugins
   toolchain · termux · bridge · python · tools (ToolRunner)
   + services: permissions, monitor, resources, languages, adb, logcat, crashlogs
```
Rules: modules talk through the **EventBus** (`project.created`, `run.output`, `server.changed`, `plugins.changed`,
`ui.consent.request`, ...) or the **ServiceRegistry**; the UI never blocks: long work goes through `TaskManager` and
results return on the Kivy main thread via the dispatcher (`Clock.schedule_once`). Event handlers registered with
`on_main=True` are dispatched to the main thread.

Package map (see MODULES.md for each interface): `core, security, storage, utilities, resource_manager, project_manager,
language_support, compiler_manager (RunService, execution targets), terminal (+ VT emulator), shell_environment,
package_manager, plugin_system, linux_manager, server_manager, ai_runtime, android_toolkit, gui_compatibility,
application_framework, integrations (git), debugger, framework_manager, interface`.

## Threading
* Kivy main thread: UI only. `TaskManager` (thread pool) for: installs, downloads, diagnostics, server start/stop,
  run/build, console commands. Terminal PTY reading has its own thread per session.
* `ConsentGateway.request()` blocks and **must only be called from worker threads**; the UI answers via a dialog.

## Process communication
stdin/stdout (build/run, servers, LSP), PTY (terminal), HTTP (GitHub API, local servers), TCP loopback JSON-lines IPC
(Application Framework), JNI via PyJNIus (device info, intents). Protocols are in API.md.

## Toolchain layer (added)
```
ToolRunner / ndx.tools.run ──> Resolver (managed>project>external>system>proot>termux, per-runtime PATH)
                                   │                       │
                         ToolchainManager (install/verify/update/remove, shims in bin/)
                                   │
 TerminalBridge ── host | android | termux | linux:<id> ──> PtySession / run_process (existing)
 PythonRuntime ── embedded | host | venv | termux | linux:<id>
 Supervisor (service process) <── files ──> BackgroundLink (UI process)
 IpcServer: loopback, token + scopes, per-launch revocable tokens for external scripts (IPC.md)
```
