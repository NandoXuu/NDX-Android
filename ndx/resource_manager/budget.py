"""Global resource budgets and per-service request gates.

Honesty note: budgets here are *accounting and admission control* done by NDX.
RAM budgets are advisory reservations and monitoring thresholds; hard limits require OS enforcement."""
import contextlib
import itertools
import os
import threading
import time

STORAGE_MARGIN = 1.0          # callers already add their own safety factor
KINDS = ("download", "extract", "package", "compiler", "linux", "ai", "server", "process")


class _Op:
    """A tracked long-running operation (download, extraction, compiler run, ...)."""

    def __init__(self, oid, kind, name):
        self.id, self.kind, self.name = oid, kind, name
        self.started = time.time()
        self.done = self.total = None
        self.finished = None

    def update(self, done=None, total=None):
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total

    def describe(self):
        d = {"id": self.id, "kind": self.kind, "name": self.name, "elapsed": round(time.time() - self.started, 1)}
        if self.done is not None:
            d["done"] = self.done
        if self.total:
            d["total"] = self.total
        return d


class ResourceManager:
    def __init__(self, ram_budget_mb=1024, bus=None, monitor=None, storage_path=None, max_heavy_tasks=2):
        self.ram_budget_mb = ram_budget_mb
        self.bus = bus
        self.monitor = monitor
        self.storage_path = storage_path
        self.max_heavy_tasks = max_heavy_tasks
        self._alloc = {}
        self._ops = {}
        self._op_ids = itertools.count(1)
        self._lock = threading.Lock()

    # ---- tracked operations (downloads, extraction, compilers, Linux envs, AI models, servers)
    @contextlib.contextmanager
    def track(self, kind, name):
        op = _Op(next(self._op_ids), kind, name)
        with self._lock:
            self._ops[op.id] = op
        if self.bus:
            self.bus.publish("resources.op_started", op.describe(), "resources")
        try:
            yield op
        finally:
            with self._lock:
                self._ops.pop(op.id, None)
            if self.bus:
                self.bus.publish("resources.op_finished", {"id": op.id, "kind": kind, "name": name}, "resources")

    def operations(self, kind=None):
        with self._lock:
            return [o.describe() for o in self._ops.values() if kind in (None, o.kind)]

    # ---- storage admission
    def free_storage(self, path=None):
        """Free bytes on the volume holding `path`, or None when it cannot be measured."""
        target = str(path or self.storage_path or ".")
        while target and not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        try:
            st = os.statvfs(target)
            return st.f_bavail * st.f_frsize
        except (OSError, AttributeError):
            return None

    def check_storage(self, path, required_bytes):
        """-> (ok, info). Unknown free space is reported as unknown (ok=True with a warning): never as a number."""
        free = self.free_storage(path)
        if free is None:
            return True, {"free": None, "required": int(required_bytes), "quality": "unavailable",
                          "message": "free storage could not be measured; continuing without a space check"}
        ok = free >= required_bytes * STORAGE_MARGIN
        msg = "%d MB free, about %d MB needed" % (free // 2 ** 20, int(required_bytes) // 2 ** 20)
        if not ok and self.bus:
            self.bus.publish("resources.rejected", {"owner": "storage", "bytes": int(required_bytes)}, "resources")
        return ok, {"free": free, "required": int(required_bytes), "quality": "exact", "message": msg}

    # ---- admission for resource-heavy processes
    def admit(self, kind, ram_mb=0):
        """Advice before starting a heavy process. -> {"ok": bool, "warnings": [...], "blocked": [...]}.
        Warnings are advisory (the user may continue); blocked entries are hard refusals from the RAM budget."""
        warnings, blocked = [], []
        heavy = [o for o in self.operations() if o["kind"] in ("compiler", "linux", "ai", "extract", "process")]
        if len(heavy) >= self.max_heavy_tasks:
            warnings.append("%d resource-heavy tasks are already running (%s)" %
                            (len(heavy), ", ".join(o["name"] for o in heavy[:3])))
        if ram_mb and not self.can_reserve(ram_mb):
            blocked.append("RAM budget: %d MB requested, %d MB of %d MB already reserved" %
                           (ram_mb, self.reserved_mb(), self.ram_budget_mb))
        if ram_mb and self.monitor is not None:
            avail = self.monitor.memory()["available"]
            if avail["quality"] == "exact" and avail["value"] is not None and avail["value"] < ram_mb * 1024 * 1024:
                warnings.append("only %d MB of RAM is available right now (about %d MB requested)" %
                                (avail["value"] // 2 ** 20, ram_mb))
        return {"ok": not blocked, "warnings": warnings, "blocked": blocked}

    def summary(self):
        return {"ram_budget_mb": self.ram_budget_mb, "reserved_mb": self.reserved_mb(),
                "allocations": self.allocations(), "operations": self.operations()}

    def reserved_mb(self):
        with self._lock:
            return sum(self._alloc.values())

    def can_reserve(self, mb):
        return self.reserved_mb() + mb <= self.ram_budget_mb

    def reserve(self, owner, mb):
        with self._lock:
            used = sum(v for k, v in self._alloc.items() if k != owner)
            if used + mb > self.ram_budget_mb:
                if self.bus:
                    self.bus.publish("resources.rejected", {"owner": owner, "mb": mb}, "resources")
                return False
            self._alloc[owner] = mb
            return True

    def release(self, owner):
        with self._lock:
            self._alloc.pop(owner, None)

    def allocations(self):
        with self._lock:
            return dict(self._alloc)


class RequestGate:
    """Concurrency + queue limit. acquire() returns False when the request
    must be rejected (queue full or wait timed out)."""

    def __init__(self, max_concurrency=4, queue_limit=16, timeout=10.0):
        self.max_concurrency = max_concurrency
        self.queue_limit = queue_limit
        self.timeout = timeout
        self._active = 0
        self._waiting = 0
        self.rejected = 0
        self.served = 0
        self._cv = threading.Condition()

    def acquire(self):
        deadline = time.time() + self.timeout
        with self._cv:
            if self._active >= self.max_concurrency:
                if self._waiting >= self.queue_limit:
                    self.rejected += 1
                    return False
                self._waiting += 1
                try:
                    while self._active >= self.max_concurrency:
                        left = deadline - time.time()
                        if left <= 0:
                            self.rejected += 1
                            return False
                        self._cv.wait(left)
                finally:
                    self._waiting -= 1
            self._active += 1
            return True

    def release(self):
        with self._cv:
            self._active = max(0, self._active - 1)
            self.served += 1
            self._cv.notify()

    def stats(self):
        with self._cv:
            return {"active": self._active, "waiting": self._waiting,
                    "served": self.served, "rejected": self.rejected}
