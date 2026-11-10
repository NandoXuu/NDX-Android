"""Run a Python script INSIDE the NDX process (the interpreter embedded in the APK).

Why: on Android the APK's interpreter is a shared library loaded by the app, not a `python3` executable, so
`python3 main.py` from any shell cannot use it. The only way to use it is to run code in the app process.

Limits (honest, documented in docs/PYTHON.md):
- the script shares the NDX process: it can read NDX memory/state. Treat it like a plugin: run trusted code only.
- stdout/stderr are routed per thread; os.chdir / sys.argv / os.environ are process-global, so only ONE embedded
  script runs at a time (a lock serialises them).
- cancellation/timeouts use an asynchronous exception: they cannot interrupt code blocked in native calls
  (sleep inside C, sockets, ...). A script that cannot be interrupted keeps running and the runner reports it.
- no stdin."""
import ctypes
import io
import os
import runpy
import sys
import threading
import time
import traceback


class Cancelled(BaseException):
    pass


class _Router(io.TextIOBase):
    """sys.stdout/sys.stderr replacement: threads that registered a sink write there, everyone else untouched."""

    def __init__(self, orig):
        self.orig = orig
        self.sinks = {}

    def writable(self):
        return True

    def write(self, s):
        sink = self.sinks.get(threading.get_ident())
        if sink is not None:
            sink(s)
        elif self.orig is not None:
            return self.orig.write(s)
        return len(s)

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.orig, name)


_install_lock = threading.Lock()


def _install():
    with _install_lock:
        for name in ("stdout", "stderr"):
            cur = getattr(sys, name)
            if not isinstance(cur, _Router):
                setattr(sys, name, _Router(cur))
    return sys.stdout, sys.stderr


class EmbeddedRunner:
    _run_lock = threading.Lock()

    def run(self, script, args=(), cwd=None, env=None, on_output=None, cancel=None, timeout=None):
        """-> dict(returncode, stdout, stderr, timed_out, cancelled, error). Never raises for script errors."""
        out_router, err_router = _install()
        res = {"returncode": None, "stdout": "", "stderr": "", "timed_out": False, "cancelled": False, "error": ""}
        if not self._run_lock.acquire(timeout=5):
            res.update(returncode=1, error="the embedded Python runner is busy (another script is still running "
                                           "or could not be interrupted)")
            return res
        outb, errb = [], []
        script = os.path.realpath(script)
        cwd = cwd or os.path.dirname(script)
        state = {"code": None}

        def sink(buf, name):
            def write(s):
                buf.append(s)
                if on_output:
                    on_output(name, s)
            return write

        def work():
            tid = threading.get_ident()
            state["tid"] = tid
            out_router.sinks[tid] = sink(outb, "stdout")
            err_router.sinks[tid] = sink(errb, "stderr")
            saved = (os.getcwd(), list(sys.argv), list(sys.path), dict(os.environ), set(sys.modules))
            try:
                os.chdir(cwd)
                sys.argv = [script] + list(args)
                sys.path.insert(0, os.path.dirname(script))
                for k, v in (env or {}).items():
                    os.environ[k] = v
                try:
                    runpy.run_path(script, run_name="__main__")
                    state["code"] = 0
                except SystemExit as e:
                    c = e.code
                    state["code"] = c if isinstance(c, int) else (0 if c is None else (print(c, file=sys.stderr) or 1))
                except Cancelled:
                    state["cancelled_exc"] = True
                    state["code"] = 130
                except BaseException:
                    traceback.print_exc()
                    state["code"] = 1
            finally:
                try:
                    os.chdir(saved[0])
                except OSError:
                    pass
                sys.argv, sys.path[:] = saved[1], saved[2]
                os.environ.clear()
                os.environ.update(saved[3])
                root = os.path.dirname(script)
                for name in set(sys.modules) - saved[4]:          # let the next run see edited project modules
                    f = getattr(sys.modules.get(name), "__file__", None) or ""
                    if f.startswith(root):
                        sys.modules.pop(name, None)
                out_router.sinks.pop(tid, None)
                err_router.sinks.pop(tid, None)
                state["done"] = True
                self._run_lock.release()

        th = threading.Thread(target=work, daemon=True, name="ndx-embedded-python")
        th.start()
        end = None if timeout is None else time.time() + timeout
        interrupted = False
        while th.is_alive():
            th.join(0.05)
            if not interrupted and ((cancel is not None and cancel.is_set()) or (end and time.time() > end)):
                res["cancelled"] = bool(cancel is not None and cancel.is_set())
                res["timed_out"] = bool(end and time.time() > end and not res["cancelled"])
                interrupted = True
                self._async_raise(state.get("tid"))
                th.join(3)
                if th.is_alive():
                    res["error"] = ("the script could not be interrupted (it is blocked in native code) and keeps "
                                    "running inside NDX; further embedded runs are refused until it ends")
                    break
        res["stdout"], res["stderr"] = "".join(outb), "".join(errb)
        res["returncode"] = state["code"] if state.get("done") else None
        return res

    @staticmethod
    def _async_raise(tid):
        if tid is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(Cancelled))
