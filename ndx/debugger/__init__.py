"""Debugger integration registry. NDX does not implement a debug UI yet: this module
only records which debuggers each language lists and whether they are installed."""
from ..toolchain_manager.manager import host_which as which


def debugger_status(language):
    cmd = language.debugger.split()[0] if language.debugger else ""
    if not cmd:
        return {"debugger": "", "installed": False, "ui": "not implemented"}
    return {"debugger": language.debugger, "installed": which(cmd) is not None, "ui": "not implemented"}
