# Language integration guide
Built-in languages: Python, Lua, C, C++, Rust, Java, Kotlin, JavaScript, TypeScript, HTML, CSS, Shell (+ JSON, TOML, Markdown).
A `Language` defines: `extensions, lexer, comment, keywords, tools, build, run, lsp, debugger, dependency_file, environment`.
Templates use `{project} {file} {name} {out}`; commands are split with `shlex` and executed **without a shell**.
Add languages with a declarative plugin (PLUGINS.md) or `ctx.register_language(...)`.

Android reality: the Android host has no compilers. Run/build targets: `host` (Android shell) or `proot:<env>`
(Linux environment). Pick the target with the swap icon in the editor. Syntax highlighting, completion
(keywords + buffer words) and basic diagnostics work without any toolchain. Semantic completion/diagnostics require an LSP
server installed in the target environment; NDX's `LspClient` is implemented, but wiring to the editor UI is **not** done yet.
Code folding: not implemented (the editor widget does not support it).
