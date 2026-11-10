# NDX IPC bindings

Clients for the authenticated NDX IPC server (`ndx/application_framework/ipc.py`, protocol in `docs/IPC.md`).

| File | Language | Needs | Status |
|---|---|---|---|
| `ndxapp.py` | Python 3 | stdlib | tested against the real server (tests/test_ipc_security.py) |
| `ndxapp.js` | Node.js | stdlib `net` | smoke-tested against the real server if `node` is present (see tests); not tested on Android |
| `ndx.lua` | Lua 5.1-5.4 / LuaJIT | luasocket | **untested**: no Lua interpreter was available when written |

All clients read `NDX_IPC_PORT` and `NDX_IPC_TOKEN` from the environment (NDX sets them for scripts it launches), send
the `auth` request first, and expose `tools_run(tool, args_list, ...)`. Pass arguments as a list; never build shell strings.
A token only grants the scopes it was issued with; a missing scope returns error code `forbidden`.
