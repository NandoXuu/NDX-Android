"""Blocking user-consent gateway usable from WORKER threads only.

The UI subscribes to `ui.consent.request`; with no UI attached, the default
answer is DENY (nothing sensitive happens silently). Never call request()
from the UI thread: it blocks until the user answers."""
import threading


class ConsentGateway:
    def __init__(self, bus, audit=None):
        self.bus = bus
        self.audit = audit
        self._pending = []
        self._lock = threading.Lock()

    def request(self, title, message, details=None, timeout=300):
        if not self.bus.has_subscribers("ui.consent.request"):
            return False
        box = {"answer": False}
        done = threading.Event()

        def respond(answer):
            box["answer"] = bool(answer)
            done.set()

        with self._lock:
            self._pending.append(done)
        try:
            self.bus.publish("ui.consent.request",
                             {"title": title, "message": message, "details": details or [], "respond": respond},
                             "consent")
            done.wait(timeout)
        finally:
            with self._lock:
                if done in self._pending:
                    self._pending.remove(done)
        if self.audit:
            self.audit("consent", title=title, answer=box["answer"])
        return box["answer"]

    def cancel_all(self):
        """Deny everything still waiting (called on shutdown)."""
        with self._lock:
            pending, self._pending = self._pending, []
        for ev in pending:
            ev.set()
