from .vt import VTScreen
from .keys import encode_key, SPECIAL_KEYS
from .pty_session import PtySession
from .manager import TerminalManager
from .console_session import ConsoleSession
from .macros import parse_combo, combo_bytes
from .process import CommandResult, run_process
from .bridge import (TerminalBridge, Session, PtyBridgeSession, PipeSession, NativeProcessBackend, AndroidShellBackend,
                     PRootBackend, LinuxEnvironmentBackend, TermuxBackend, BackendUnavailable)


def _bridge():
    from ..core import active
    core = active()
    if core is None:
        raise RuntimeError("NDX core is not running")
    return core.services.require("bridge")


def open(environment="host", **kw):          # noqa: A001  (public API: ndx.terminal.open(environment="ubuntu"))
    """Open an interactive session: session = ndx.terminal.open(environment="ubuntu")."""
    return _bridge().open(environment, **kw)


def run(argv, environment="host", **kw):
    """Run a command to completion (argv list, never a shell string) -> CommandResult."""
    return _bridge().run(argv, environment, **kw)
