"""Logging: rotating file, in-memory ring for the UI, JSONL audit log."""
import collections
import json
import logging
import logging.handlers
import threading
import time


class RingHandler(logging.Handler):
    def __init__(self, capacity=500):
        super().__init__()
        self.records = collections.deque(maxlen=capacity)

    def emit(self, record):
        try:
            self.records.append((record.created, record.levelname, record.name, self.format(record)))
        except Exception:
            pass


class LoggingService:
    def __init__(self, logs_dir, level=logging.INFO):
        self.logs_dir = logs_dir
        self.ring = RingHandler()
        self._audit_lock = threading.Lock()
        root = logging.getLogger("ndx")
        root.setLevel(level)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        for h in list(root.handlers):
            if getattr(h, "_ndx", False):
                root.removeHandler(h)
                h.close()
        try:
            fh = logging.handlers.RotatingFileHandler(
                str(logs_dir / "ndx.log"), maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            fh._ndx = True
            root.addHandler(fh)
        except OSError:
            pass
        self.ring.setFormatter(fmt)
        self.ring._ndx = True
        root.addHandler(self.ring)

    def audit(self, action, **details):
        """Append a record of a sensitive operation (install, grant, spawn...)."""
        rec = {"ts": time.time(), "action": action, **details}
        with self._audit_lock:
            try:
                with open(self.logs_dir / "audit.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            except OSError:
                pass
        return rec

    def recent(self, n=100):
        return list(self.ring.records)[-n:]
