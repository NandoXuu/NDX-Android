# Python execution targets

NDX never assumes `python3` means the interpreter inside the APK. **The APK's embedded interpreter cannot be run as
`python3 main.py` from a normal Android shell**; it is a library loaded by the app process.

| target | what runs the script | requirements |
|---|---|---|
| `embedded` | in-process (`runpy` in a worker thread) on Android; a subprocess of `sys.executable` on desktop | bundled packages only |
| `host` | an external python3 managed by the Toolchain Manager | `ndx tool install python3` |
| `venv` | `<project>/.venv/bin/python3` | create: `ndx python venv <project>` (needs external python) |
| `termux` | Termux python | script in shared storage |
| `linux:<id>` | python3 in a PRoot env; project at `/project` | script inside the project |

`ndx python targets [project]` shows availability and what `auto` chooses and why (a venv wins; missing requirements in the
embedded runtime move the project to an external target, or warn when none exists). `ndx python run [--target T] script args`.

**Embedded limits (honest):** same process as NDX (run trusted code only; non-user callers need consent); one script at a time
(a lock; cwd/argv/environ are process-global and restored afterwards); no stdin; stop/timeout use an asynchronous exception
and cannot interrupt code blocked in native calls (reported, and later embedded runs are refused until it ends); no pip.
`RunService` routes `python3 file.py` run configurations to the embedded target when no external python resolves.
