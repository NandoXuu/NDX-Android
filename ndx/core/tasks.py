"""Background task manager: keeps long work off the Kivy main thread."""
import itertools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("ndx.tasks")


class Task:
    def __init__(self, tid, name):
        self.id = tid
        self.name = name
        self.state = "pending"   # pending running done failed cancelled
        self.result = None
        self.error = None
        self.progress = 0.0
        self.started = None
        self.finished = None
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    @property
    def cancelled(self):
        return self.cancel_event.is_set()


class TaskManager:
    def __init__(self, bus=None, workers=4, main_dispatcher=None):
        self.bus = bus
        self.main_dispatcher = main_dispatcher or (lambda fn: fn())
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ndx-task")
        self._ids = itertools.count(1)
        self.tasks = {}
        self._lock = threading.Lock()

    def submit(self, fn, *args, name="task", on_done=None, on_error=None, pass_task=False, **kwargs):
        """Run fn in a worker thread. Callbacks run via the main dispatcher."""
        task = Task(next(self._ids), name)
        with self._lock:
            self.tasks[task.id] = task
            if len(self.tasks) > 200:
                for tid in [t for t, v in self.tasks.items() if v.state in ("done", "failed", "cancelled")][:100]:
                    self.tasks.pop(tid, None)

        def runner():
            task.state, task.started = "running", time.time()
            self._emit("task.started", task)
            cb, arg = None, None
            try:
                task.result = fn(task, *args, **kwargs) if pass_task else fn(*args, **kwargs)
                task.state = "cancelled" if task.cancelled else "done"
                cb, arg = on_done, task.result
            except Exception as exc:
                log.exception("task %s failed", name)
                task.state, task.error = "failed", exc
                cb, arg = on_error, exc
            task.finished = time.time()
            self._emit("task.finished", task)
            if cb:
                self.main_dispatcher(lambda: cb(arg))

        self._pool.submit(runner)
        return task

    def _emit(self, name, task):
        if self.bus:
            self.bus.publish(name, {"id": task.id, "name": task.name, "state": task.state}, "tasks")

    def active(self):
        return [t for t in self.tasks.values() if t.state in ("pending", "running")]

    def shutdown(self):
        for t in self.active():
            t.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
