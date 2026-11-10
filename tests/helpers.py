import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ndx.core import NDXCore  # noqa: E402

EXAMPLES = os.path.join(ROOT, "plugins", "examples")


class CoreCase(unittest.TestCase):
    consent_answer = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = NDXCore(self.tmp.name)
        # headless "UI" that answers consent requests
        self.core.bus.subscribe("ui.consent.request", lambda e: e.payload["respond"](self.consent_answer))
        self.core.start()

    def tearDown(self):
        self.core.stop()
        self.tmp.cleanup()
