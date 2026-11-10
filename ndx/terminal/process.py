"""Shared non-interactive process runner (argv only, never a shell string).

Used by the Terminal Bridge backends, RunService and the tool runner so there is ONE implementation of
timeouts, cancellation and process-group termination."""
import codecs
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass
class CommandResult:
    argv: list
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self):
        return self.returncode == 0 and not self.timed_out and not self.cancelled and not self.error

    @property
    def output(self):
        return self.stdout + self.stderr


def kill_group(proc, grace=2.0):
    """SIGTERM the whole process group, then SIGKILL if it does not exit."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            pass


def run_process(argv, cwd=None, env=None, timeout=None, stdin_data=None, cancel_event=None, on_output=None,
                merge_stderr=False, register=None, max_output=8 * 1024 * 1024):
    """Run `argv` to completion. on_output(stream, text) is called as output arrives ('stdout' | 'stderr')."""
    res = CommandResult(argv=list(argv))
    t0 = time.time()
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv) \
            or any("\0" in a for a in argv):
        res.error = "argv must be a non-empty list of strings"
        res.returncode = 2
        return res
    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                                start_new_session=True)
    except FileNotFoundError:
        res.error, res.returncode = "command not found: %s" % argv[0], 127
        res.duration = time.time() - t0
        return res
    except PermissionError as e:
        res.error, res.returncode = "permission denied: %s (%s)" % (argv[0], e), 126
        res.duration = time.time() - t0
        return res
    except OSError as e:
        res.error, res.returncode = "cannot start %s: %s" % (argv[0], e), 126
        res.duration = time.time() - t0
        return res
    if register:
        register(proc)
    bufs = {"stdout": [], "stderr": []}
    sizes = {"stdout": 0, "stderr": 0}

    def pump(stream, name):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                chunk = stream.read1(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            text = dec.decode(chunk)
            if sizes[name] < max_output:
                bufs[name].append(text)
                sizes[name] += len(text)
            if on_output:
                try:
                    on_output(name, text)
                except Exception:
                    pass

    threads = [threading.Thread(target=pump, args=(proc.stdout, "stdout"), daemon=True)]
    if not merge_stderr:
        threads.append(threading.Thread(target=pump, args=(proc.stderr, "stderr"), daemon=True))
    for t in threads:
        t.start()
    if stdin_data is not None:
        try:
            proc.stdin.write(stdin_data.encode("utf-8") if isinstance(stdin_data, str) else stdin_data)
            proc.stdin.close()
        except OSError:
            pass
    deadline = (t0 + timeout) if timeout else None
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            res.cancelled = True
            kill_group(proc)
            break
        if deadline and time.time() > deadline:
            res.timed_out = True
            kill_group(proc)
            break
        time.sleep(0.02)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        kill_group(proc, 1.0)
    for t in threads:
        t.join(2)
    # reap stragglers left in the group (background children holding the pipes)
    try:
        os.killpg(proc.pid, signal.SIGKILL) if (res.cancelled or res.timed_out) else None
    except (OSError, ProcessLookupError):
        pass
    res.returncode = proc.returncode
    res.stdout, res.stderr = "".join(bufs["stdout"]), "".join(bufs["stderr"])
    res.duration = time.time() - t0
    if res.timed_out:
        res.error = "timed out after %ss" % timeout
    return res
