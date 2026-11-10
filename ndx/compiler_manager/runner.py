"""Build/run execution. Commands run without a shell (argv list) on an
ExecutionTarget: the host Android shell environment or a PRoot distro."""
import shlex
import subprocess
import threading

from ..core.registry import Module


def expand_command(template, project_path, file_path="", out_path=""):
    import os
    name = os.path.splitext(os.path.basename(file_path))[0] if file_path else ""
    s = template.replace("{project}", project_path).replace("{file}", file_path) \
        .replace("{out}", out_path or (project_path + "/" + (name or "a.out"))).replace("{name}", name)
    return shlex.split(s)


class ExecutionTarget:
    id = "target"
    title = "Target"

    def wrap(self, argv, cwd):
        raise NotImplementedError

    def available(self):
        return True

    def env(self):
        """Extra environment for the process (e.g. PROOT_LOADER, the managed-toolchain PATH)."""
        return {}


class HostTarget(ExecutionTarget):
    id = "host"
    title = "Android shell / host"

    def env(self):
        from ..toolchain_manager import active
        tc = active()
        return tc.environment("host") if tc is not None else {}

    def wrap(self, argv, cwd):
        from ..toolchain_manager import active
        tc = active()
        if tc is not None and argv and "/" not in argv[0]:
            resolved = tc.resolve_path(argv[0])
            if resolved:
                argv = [resolved] + list(argv[1:])
        return argv, cwd


class RunService(Module):
    name = "runner"
    title = "Build & Run"

    def __init__(self, bus, tasks, audit=None, consent=None):
        self.bus = bus
        self.tasks = tasks
        self.audit = audit or (lambda *a, **k: None)
        self.consent = consent
        self.targets = {"host": HostTarget()}
        self.procs = {}

    def register_target(self, target):
        self.targets[target.id] = target

    def run(self, argv, cwd, target_id="host", label="run", trusted=False):
        """Start a build/run process; output is streamed as `run.output` events.
        Consent is requested in the worker thread (never blocks the UI)."""
        target = self.targets[target_id]

        def finish(code, **extra):
            self.bus.publish("run.finished", {"label": label, "code": code, **extra}, "runner")
            return code

        def job(task):
            if not trusted and self.consent is not None:
                ok = self.consent.request("Run project code?",
                                          "This executes code from your project on this device.",
                                          [" ".join(argv), "Target: " + target.title])
                if not ok:
                    return finish(None, denied=True)
            try:
                argv2, cwd2 = target.wrap(argv, cwd)
            except Exception as exc:
                self.bus.publish("run.output", {"label": label, "text": "error: %s\n" % exc}, "runner")
                return finish(127)
            self.audit("process.spawn", argv=argv2, cwd=str(cwd2), target=target_id)
            if getattr(target, "inline", False):          # runs inside the NDX process (embedded Python)
                import threading as _t
                ev = _t.Event()

                def _watch():
                    while not ev.is_set() and not getattr(task, "finished", False):
                        if task.cancelled:
                            ev.set()
                        ev.wait(0.1)
                _t.Thread(target=_watch, daemon=True).start()
                code = target.run_inline(argv2, cwd2, lambda t: self.bus.publish("run.output", {"label": label, "text": t},
                                                                                "runner"), ev)
                ev.set()
                return finish(code)
            try:
                import os
                env2 = {**os.environ, **target.env()}
                p = subprocess.Popen(argv2, cwd=cwd2, env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1)
            except (OSError, ValueError) as exc:
                self.bus.publish("run.output", {"label": label, "text": "error: %s\n" % exc}, "runner")
                return finish(127)
            self.procs[task.id] = p
            for line in p.stdout:
                self.bus.publish("run.output", {"label": label, "text": line}, "runner")
                if task.cancelled:
                    p.terminate()
                    break
            code = p.wait()
            self.procs.pop(task.id, None)
            return finish(code)

        return self.tasks.submit(job, name="run:" + label, pass_task=True)
