"""NDX console session: same interface as PtySession, but runs `ndx ...` and
plugin commands in-process (no OS shell)."""
from .vt import VTScreen

PROMPT = "\x1b[36mndx>\x1b[0m "


class ConsoleSession:
    def __init__(self, core, rows=24, cols=80, on_update=None):
        self.core = core
        self.screen = VTScreen(rows, cols, 2000)
        self.on_update = on_update or (lambda: None)
        self.buf = ""
        self.history = []
        self.exit_code = None
        self.title = "NDX console"
        self.busy = False

    def start(self):
        self._emit("NDX console - type 'help'. Package manager: ndx install|list|search|doctor\n")
        self._prompt()
        return self

    def _emit(self, text):
        self.screen.feed(text.replace("\r\n", "\n").replace("\n", "\r\n"))
        self.on_update()

    def _prompt(self):
        self.screen.feed(PROMPT)
        self.on_update()

    def write(self, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        for ch in data:
            if self.busy:
                continue
            if ch in "\r\n":
                self._run()
            elif ch in "\x7f\b":
                if self.buf:
                    self.buf = self.buf[:-1]
                    self.screen.feed("\b \b")
            elif ch == "\x03":
                self.buf = ""
                self.screen.feed("^C\r\n")
                self._prompt()
            elif ch >= " ":
                self.buf += ch
                self.screen.feed(ch)
        self.on_update()

    def _run(self):
        line, self.buf = self.buf.strip(), ""
        self.screen.feed("\r\n")
        if line:
            self.history.append(line)
        if line == "clear":
            self.screen.feed("\x1b[2J\x1b[H")
            self._prompt()
            return
        if not line:
            self._prompt()
            return
        self.busy = True
        console = self.core.services.require("console")

        def work():
            return console.execute(line, self._emit)

        def done(_):
            self.busy = False
            self._prompt()

        self.core.tasks.submit(work, name="console", on_done=done, on_error=lambda e: (self._emit("error: %s\n" % e), done(None)))

    def resize(self, rows, cols):
        self.screen.resize(rows, cols)

    def alive(self):
        return True

    def snapshot_markup(self, **kw):
        return self.screen.markup(**kw)

    def terminate(self):
        pass
