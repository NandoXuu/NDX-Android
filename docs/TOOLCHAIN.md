# Toolchain Manager

One place that knows which external tools NDX can use, where they are, and how to install, validate, update and remove
the ones it manages. **Users never copy executables into `bin/` by hand**: `ndx tool install <id>` does it.

## Commands (`ndx tool ...`)
```
ndx tool list [--installed]        ndx tool info <id>         ndx tool resolve <command> [--all]
ndx tool install <id> [--env ENV] [--method download|native_lib|linux_package|termux_package]
                      [--from-file PATH] [--sha256 HEX] [--allow-unverified] [--force] [--yes]
ndx tool remove <id> [--scope host|env:NAME|termux] [--force] [--yes]
ndx tool update [id]   ndx tool use <id> <version>   ndx tool doctor [id]
ndx tool profile list|show <name>|install <name>     ndx tool add-manifest <file.json>
ndx tool cache clear   ndx tool probe
ndx run [--env ENV] [--cwd DIR] [--timeout S] <tool> [args...]
```
## Install pipeline (transactional)
manifest validation -> architecture/platform check (ABI, ELF class/machine when the file is an ELF) -> storage check
(Resource Manager) -> download from an **allow-listed host** into staging -> checksum -> safe extraction (no path escape,
no device nodes, no writes through symlinks) -> executable probe (`--version` with timeout, must match the manifest regex)
-> atomic move into `<ndx_home>/tools/<id>/<version>/` -> shim in `<ndx_home>/bin/` -> registry entry. Any failure rolls
back to the previous version; staging is always cleaned. A tool is reported **installed only after the probe ran**.

## Checksums and unverified downloads
* Pinned `sha256` in the manifest, or a sidecar published by upstream (Alpine `.sha256`, Ubuntu `SHA256SUMS`).
* No checksum available: the download is refused unless you pass `--allow-unverified`/confirm the explicit consent prompt.
  The computed hash is recorded (trust-on-first-use, state `unverified-approved`) and later compared.
* **Catalog verification:** the built-in catalog URLs/versions were written offline and could **not** be verified
  (no network in the build environment). Entries without a pinned checksum will ask for that consent. Check/update them
  with `ndx tool add-manifest` or by editing `ndx/toolchain_manager/catalog.py` before relying on them.

## Install methods
`native_lib` (a binary shipped as a library in the APK's native-lib dir; none is bundled in this build), `download`,
`linux_package` (apk/apt inside a managed Linux environment), `termux_package` (pkg in Termux, optional), `manual`
(`--from-file`, verified like any download).

## Resolver
`ndx tool resolve <cmd> [--all]` lists every candidate and why one wins. Default priority (configurable):
**managed > project > external (user-authorised path) > system > proot (Linux env) > termux**. Each runtime
(host / android / termux / linux) has its own PATH; a Linux-env binary is never put on the Android PATH.

## Scripts: `ndx.tools.run`
```python
from ndx.tools import run
run("git", ["status"], cwd=project)          # argv list, never a shell string
```
Only declared tools; first use asks for consent; capability-checked; output size/timeout capped. Over IPC see IPC.md.

## Profiles
`ndx tool profile list|show|install` install groups (e.g. a C/C++ set, a Python set) through the same pipeline.
