# Plugin development guide

## Package layout
```
my-plugin/
  ndx-plugin.toml      (required)
  main.py              (only for code plugins)
```
## Manifest
```toml
[plugin]
id = "rust-support"            # ^[a-z0-9][a-z0-9-]{1,63}$
name = "Rust Support"
version = "1.0.2"
author = "Developer"
description = "Rust language integration"
ndx_version = ">=1.0.0"

[plugin.type]
category = "language"          # language compiler framework library gui-toolkit debugger
                               # language-server theme integration shell-tool runtime-environment
[language]                     # declarative: no code runs
name = "Rust"
extensions = [".rs"]
lexer = "rust"                 # Pygments alias
keywords = ["fn", "let"]
[compiler]
command = "rustc"
build = "rustc {file} -o {out}"
run = "{out}"

[permissions]                  # code plugins only
request = ["terminal.command", "event.subscribe"]
[entry]
module = "main.py"
class = "Plugin"
[dependencies]
other-plugin = ">=1.0"
```
`[theme]` plugins give `pygments_style`, `background`, `foreground`.

## Code plugins
```python
from ndx.plugin_system import PluginBase
class Plugin(PluginBase):
    def on_load(self, ctx):
        ctx.register_terminal_command("hello", lambda args, out: out("hi\n") or 0)
        ctx.subscribe("project.created", self.on_project)
```
`PluginContext`: `subscribe, publish, get_setting, register_terminal_command, register_language, add_panel, log`.
Each call checks a capability: `event.subscribe event.publish config.read config.write terminal.command language.register
compiler.register ui.panel fs.read fs.write net.client net.server process.spawn`.

## Security model (read this)
* Installing never executes plugin code. Packages are validated (manifest, size, no symlinks, no path traversal),
  checksummed when a `.sha256` release asset exists, and require your confirmation.
* Declarative plugins are registered without running code.
* Code plugins load only if: enabled **and** you pressed "Review & approve code" **and** Settings > "Allow plugin code" is on.
* Capability checks protect the NDX API. **They are not an OS sandbox**: Python code in-process could import `os`.
  Approve only code you trust. Signatures: hook exists, verification is not implemented ("when available").

## Publishing
Tag the GitHub repo with topic `ndx-plugin`; attach `<name>.zip` (+ `<name>.zip.sha256`) to a Release.
Install: `ndx install https://github.com/author/RustSupport [--version 1.0.2]`.
Validate locally: `python tools/validate_plugin.py path/to/plugin`.
Examples: `plugins/examples/{rust-support,solarized-theme,hello-commands}`.
