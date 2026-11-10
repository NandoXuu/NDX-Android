from dataclasses import dataclass, field


@dataclass
class Language:
    id: str
    name: str
    extensions: list
    lexer: str = "text"                 # pygments lexer alias
    comment: str = "#"
    keywords: list = field(default_factory=list)
    tools: list = field(default_factory=list)      # executables needed (any-of groups are separate entries)
    build: str = ""                     # command template; {file} {name} {out} {project}
    run: str = ""
    lsp: list = field(default_factory=list)        # argv of a language server (optional)
    debugger: str = ""
    dependency_file: str = ""
    environment: str = ""               # human text: what the toolchain needs
    source: str = "builtin"             # builtin | plugin:<id>
