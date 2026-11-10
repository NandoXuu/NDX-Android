# Troubleshooting
* **No shell / terminal fails**: Environments shows detected shells. Android always has `/system/bin/sh`; if none, report it (Settings > Logs).
* **"proot is not installed"**: `ndx tool install proot` (or Environments > Install PRoot). If that fails the message names the step (download/checksum/arch/probe); `ndx tool doctor proot` re-validates.
* **Linux env does not start**: `ndx linux check <id>`.
* **Termux not ready**: `ndx termux status` explains the state (TERMUX.md).
* **ADB**: Toolkit shows the state (unavailable / disconnected / authorization required / connected).
* **Plugin installed but inactive**: Packages shows why (`code not approved`, integrity failure). Code plugins need approval + Settings > Allow plugin code.
* **GitHub install fails**: offline or rate-limited (60 req/h unauthenticated). Use `ndx install /path/pkg.zip` instead.
* **Server won't start**: port in use, RAM budget exceeded (Servers > Resource manager), or `host` not loopback without "allow external".
* **Build errors**: BUILD.md "Expected first-build issues"; check recipes for pillow/psutil/pyjnius.
* **Crashes**: `<ndx_home>/logs/crash.log`, `ndx.log`, `audit.jsonl` (sensitive actions); `ndx doctor` in the console.
