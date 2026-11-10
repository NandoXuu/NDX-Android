# Application Framework IPC (security model)

`ndx/application_framework/ipc.py`: a JSON-lines TCP server on **127.0.0.1** that lets scripts (Python, Node, Lua, ...)
drive the Application Framework and call NDX tools. Clients: `bindings/ndxapp.py`, `ndxapp.js`, `ndx.lua`.

## Controls
| Control | Behavior |
|---|---|
| Loopback only | Binding to a non-loopback host raises `ValueError` unless `allow_remote=True` (logs a warning). Peers whose address is not loopback are dropped even if the socket is bound wider (unless `allow_remote`). |
| Authentication | First request must be `auth{token}`. Tokens: `secrets.token_urlsafe(32)`, stored only as SHA-256, compared with `hmac.compare_digest`. Wrong/missing auth: generic error; connection closed after 3 failures, with a growing delay. |
| Authorization | Scopes per token: `ui`, `kv`, `tools`, `admin`. A call outside the scopes returns `forbidden`. `require_auth=False` (tests only) never grants `admin`. |
| Allow-list | Only the methods below exist; unknown methods -> `unknown_method`. Every parameter is type/length/pattern checked (node ids `n<digits>`, kv keys `[A-Za-z0-9_.:-]{1,128}`, tool names, env var names, arg lists of strings). |
| Size limits | One line <= `max_message_bytes` (64 KiB) else the connection is dropped. kv value <= 64 KiB, tool input/output <= 1 MiB (output flagged `truncated`). |
| Rate limiting | Token bucket per connection `(burst, per_second)` = `(50, 25)` plus a global bucket at 4x. Excess -> `rate_limited` (connection kept). |
| Expiry | Absolute `ttl` per token (default 3600 s) and `idle_timeout` per connection (300 s). Revocation takes effect on the next request. |
| Clients | `max_clients` (16); extra connections get `busy`. |
| Shutdown | `stop()` stops accepting, closes every client socket, joins threads, wipes the token table. Idempotent. `server.shutdown` (scope `admin`) does the same. |
| Errors | `{"id", "error": "<message>", "code": "<code>"}`; tracebacks/paths stay in the server log. |
| Tools | `tools.*` forwards to `core.services.get("tools")` with `caller="ipc:<token label>"`, timeout capped at 600 s, arguments as a list (never a shell string). Plugin/script consent and capability rules are enforced by that service. |

## Methods
`auth{token}` · ui: `window.create{title}`, `node.add{parent,kind,props}`, `node.set{node,props}`, `node.subscribe{node,event}`,
`ui.tree{}` · kv: `services.kv_get{key}`, `services.kv_set{key,value}` · tools: `tools.run{tool,args,environment,cwd,env,timeout,input}`,
`tools.resolve{tool,environment}`, `tools.list{}` · admin: `server.status{}`, `server.shutdown{}`.
Subscribed events arrive as `{"event","node","payload"}` lines (written under a per-connection lock).

## Wiring (Core)
```python
ipc = IpcServer(core=core, audit=core.logs.audit).start()      # host defaults to 127.0.0.1
core.services.register("ipc", ipc)
tok = ipc.issue_token(("tools",), ttl=3600, label="script:" + script_name)
env.update(NDX_IPC_PORT=str(ipc.port), NDX_IPC_TOKEN=tok)       # per-launch token for a script process
...
ipc.revoke_token("script:" + script_name)                       # when the script exits
```
Never put the token on a command line (visible in `ps`); pass it through the environment of the child only.

## Limits (honest)
* The token proves "this client was started by NDX", nothing more: any process of the same app UID that can read the
  environment of the script can use it. Other Android apps cannot read it, but can *connect* to the loopback port and
  will be rejected at `auth`.
* No TLS (loopback only). `allow_remote=True` therefore sends tokens in clear text: use only on a trusted network.
* Tested on Linux/Python 3 only; not tested on Android. `ndxapp.js` is smoke-tested with Node; `ndx.lua` is untested.
