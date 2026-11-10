"""Event bus: synchronous publish/subscribe with wildcard patterns."""
import fnmatch
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("ndx.events")


@dataclass
class Event:
    name: str
    payload: dict = field(default_factory=dict)
    source: str = ""
    ts: float = field(default_factory=time.time)


class EventBus:
    """Handlers run in the publisher's thread unless on_main=True, in which
    case they go through the main-thread dispatcher (Kivy Clock in the UI)."""

    def __init__(self, main_dispatcher=None):
        self._subs = {}
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self.main_dispatcher = main_dispatcher or (lambda fn: fn())

    def subscribe(self, pattern, handler, on_main=False):
        with self._lock:
            token = next(self._ids)
            self._subs[token] = (pattern, handler, on_main)
            return token

    def unsubscribe(self, token):
        with self._lock:
            self._subs.pop(token, None)

    def publish(self, name, payload=None, source=""):
        event = Event(name, dict(payload or {}), source)
        with self._lock:
            targets = [(h, m) for (p, h, m) in self._subs.values() if fnmatch.fnmatchcase(name, p)]
        for handler, on_main in targets:
            try:
                if on_main:
                    self.main_dispatcher(lambda h=handler, e=event: self._safe(h, e))
                else:
                    self._safe(handler, event)
            except Exception:
                log.exception("dispatch failure for %s", name)
        return event

    def has_subscribers(self, pattern):
        with self._lock:
            return any(p == pattern for (p, _h, _m) in self._subs.values())

    @staticmethod
    def _safe(handler, event):
        try:
            handler(event)
        except Exception:
            log.exception("event handler failed for %s", event.name)
