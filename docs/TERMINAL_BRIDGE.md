# Terminal Bridge

One API over five backends; the existing `PtySession` / `run_process` are reused, not replaced.

| backend | runs | notes |
|---|---|---|
| `host` | Android shell / desktop host | managed tools via the host PATH |
| `android` | same as host, Android-specific env | |
| `termux` | Termux via RUN_COMMAND | no PTY (commands run in the background); optional |
| `linux:<id>` | inside a managed PRoot environment | project bound at `/project` |
| `proot` | alias used by the toolchain installer | |

`TerminalBridge.run(argv, environment, cwd, env, timeout, stdin, cancel, on_output, caller, merge_stderr, script)` -> `CommandResult`
(`returncode, stdout, stderr, timed_out, cancelled, error`). `open(...)` gives an interactive session where the backend supports a PTY.
Rules: argv arrays only (no shell strings); `caller="script"` requires consent; env keys are validated; backends report
`BackendUnavailable` with the reason and a fix; resource admission (`admit`/`track`) is applied to heavy processes.
`ndx run --env <name>` uses it.
