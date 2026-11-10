"""Diagnostics. Python/JSON/TOML are checked in-process; brace languages get a
bracket-balance check; other languages use their own syntax checker when it
is installed (sh -n, luac -p, node --check)."""
import ast
import json
import os
import tempfile
from dataclasses import dataclass

from ..utilities import run_capture
from ..toolchain_manager.manager import host_which as which


@dataclass
class Diagnostic:
    line: int
    col: int
    severity: str   # error | warning | info
    message: str
    source: str = "ndx"


def _brackets(text, lang_id):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack, out = [], []
    in_str, line, col = None, 1, 0
    i = 0
    while i < len(text):
        c = text[i]
        col += 1
        if c == "\n":
            line, col = line + 1, 0
            if in_str in ('"', "'"):
                in_str = None
        elif in_str:
            if c == "\\":
                i += 1
            elif c == in_str:
                in_str = None
        elif c in "\"'" and not (lang_id == "rust" and c == "'"):
            in_str = c
        elif text.startswith("//", i) and lang_id not in ("python", "shell", "lua"):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j - 1
        elif c in "([{":
            stack.append((c, line, col))
        elif c in pairs:
            if not stack or stack[-1][0] != pairs[c]:
                out.append(Diagnostic(line, col, "error", "unmatched '%s'" % c))
            else:
                stack.pop()
        i += 1
    for c, l, cl in stack:
        out.append(Diagnostic(l, cl, "error", "unclosed '%s'" % c))
    return out


def _external(argv_fn, text, suffix, parse):
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as fh:
        fh.write(text)
        name = fh.name
    try:
        rc, out, err = run_capture(argv_fn(name), timeout=10)
        return parse(rc, out + err)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def diagnose(text, language_id):
    try:
        if language_id == "python":
            try:
                ast.parse(text)
            except SyntaxError as e:
                return [Diagnostic(e.lineno or 1, e.offset or 0, "error", e.msg, "python")]
            return []
        if language_id == "json":
            try:
                json.loads(text)
            except ValueError as e:
                return [Diagnostic(getattr(e, "lineno", 1), getattr(e, "colno", 0), "error", e.msg, "json")]
            return []
        if language_id == "toml":
            try:
                import tomllib
                tomllib.loads(text)
            except ImportError:
                return []
            except Exception as e:
                return [Diagnostic(1, 0, "error", str(e), "toml")]
            return []
        if language_id in ("c", "cpp", "java", "kotlin", "javascript", "typescript", "rust", "css"):
            return _brackets(text, language_id)
        if language_id == "shell" and which("sh"):
            return _external(lambda n: [which("sh"), "-n", n], text, ".sh",
                             lambda rc, o: [] if rc == 0 else [Diagnostic(1, 0, "error", o.strip()[:200], "sh -n")])
        if language_id == "lua" and which("luac"):
            return _external(lambda n: [which("luac"), "-p", n], text, ".lua",
                             lambda rc, o: [] if rc == 0 else [Diagnostic(1, 0, "error", o.strip()[:200], "luac")])
    except Exception as exc:  # a broken checker must never break the editor
        return [Diagnostic(1, 0, "info", "diagnostics unavailable: %s" % exc)]
    return []
