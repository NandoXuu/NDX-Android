"""A compact VT100/xterm screen emulator (enough for shells, vim, nano, htop,
ranger, less): cursor addressing, SGR colors, erase, scroll regions, insert/
delete lines & chars, alternate screen, UTF-8. It is not a complete xterm."""
import codecs
import re

_PALETTE16 = ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
              "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]


def _xterm256(n):
    if n < 16:
        return _PALETTE16[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n // 6) % 6, n % 6
        f = lambda v: 0 if v == 0 else 55 + 40 * v
        return "#%02x%02x%02x" % (f(r), f(g), f(b))
    v = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (v, v, v)


class Cell:
    __slots__ = ("ch", "fg", "bg", "bold", "rev", "ul")

    def __init__(self, ch=" ", fg=None, bg=None, bold=False, rev=False, ul=False):
        self.ch, self.fg, self.bg, self.bold, self.rev, self.ul = ch, fg, bg, bold, rev, ul

    def style(self):
        return (self.fg, self.bg, self.bold, self.rev, self.ul)


class VTScreen:
    def __init__(self, rows=24, cols=80, scrollback=1000):
        self.rows, self.cols, self.max_scrollback = rows, cols, scrollback
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.responses = []          # bytes the terminal must send back (e.g. cursor report)
        self.app_cursor = False
        self.title = ""
        self.reset()

    def reset(self):
        self.main = self._blank_grid()
        self.alt = None
        self.grid = self.main
        self.scrollback = []
        self.cx = self.cy = 0
        self.top, self.bottom = 0, self.rows - 1
        self.cur_visible = True
        self._attr = dict(fg=None, bg=None, bold=False, rev=False, ul=False)
        self._saved = (0, 0)
        self._state = "ground"
        self._buf = ""
        self._wrap_pending = False

    def _blank_row(self):
        return [Cell() for _ in range(self.cols)]

    def _blank_grid(self):
        return [self._blank_row() for _ in range(self.rows)]

    def resize(self, rows, cols):
        if (rows, cols) == (self.rows, self.cols):
            return
        def fit(grid):
            grid = [(r + [Cell() for _ in range(cols)])[:cols] for r in grid]
            while len(grid) < rows:
                grid.append([Cell() for _ in range(cols)])
            while len(grid) > rows:
                self.scrollback.append(grid.pop(0)) if grid is self.main else grid.pop(0)
            return grid
        self.rows, self.cols = rows, cols
        self.main = fit(self.main)
        if self.alt is not None:
            self.alt = fit(self.alt)
        self.grid = self.alt if self.alt is not None else self.main
        self.top, self.bottom = 0, rows - 1
        self.cx, self.cy = min(self.cx, cols - 1), min(self.cy, rows - 1)

    # ---- input
    def feed(self, data):
        text = self.decoder.decode(data) if isinstance(data, (bytes, bytearray)) else data
        for ch in text:
            self._char(ch)

    def _char(self, ch):
        st = self._state
        if st == "ground":
            o = ord(ch)
            if ch == "\x1b":
                self._state, self._buf = "esc", ""
            elif ch == "\n" or ch == "\x0b" or ch == "\x0c":
                self._index()
            elif ch == "\r":
                self.cx, self._wrap_pending = 0, False
            elif ch == "\b":
                self.cx, self._wrap_pending = max(0, self.cx - 1), False
            elif ch == "\t":
                self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
            elif ch == "\x07" or o < 32 or o == 127:
                pass
            else:
                self._put(ch)
        elif st == "esc":
            if ch == "[":
                self._state, self._buf = "csi", ""
            elif ch == "]":
                self._state, self._buf = "osc", ""
            elif ch in "()*+#":
                self._state = "skip1"
            elif ch == "7":
                self._saved, self._state = (self.cx, self.cy), "ground"
            elif ch == "8":
                self.cx, self.cy = self._saved
                self._state = "ground"
            elif ch == "M":
                self._rindex()
                self._state = "ground"
            elif ch == "D":
                self._index()
                self._state = "ground"
            elif ch == "E":
                self.cx = 0
                self._index()
                self._state = "ground"
            elif ch == "c":
                self.reset()
            else:
                self._state = "ground"
        elif st == "skip1":
            self._state = "ground"
        elif st == "osc":
            if ch == "\x07" or ch == "\x1b":
                if self._buf[:2] in ("0;", "2;"):
                    self.title = self._buf[2:]
                self._state = "ground" if ch == "\x07" else "skip1"
            else:
                self._buf += ch
        elif st == "csi":
            if "@" <= ch <= "~":
                self._csi(self._buf, ch)
                self._state = "ground"
            else:
                self._buf += ch
                if len(self._buf) > 64:
                    self._state = "ground"

    # ---- screen ops
    def _put(self, ch):
        if self._wrap_pending:
            self.cx = 0
            self._index()
            self._wrap_pending = False
        a = self._attr
        self.grid[self.cy][self.cx] = Cell(ch, a["fg"], a["bg"], a["bold"], a["rev"], a["ul"])
        if self.cx >= self.cols - 1:
            self._wrap_pending = True
        else:
            self.cx += 1

    def _index(self):
        if self.cy == self.bottom:
            row = self.grid.pop(self.top)
            if self.grid is self.main and self.top == 0:
                self.scrollback.append(row)
                if len(self.scrollback) > self.max_scrollback:
                    del self.scrollback[:100]
            self.grid.insert(self.bottom, self._blank_row())
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _rindex(self):
        if self.cy == self.top:
            self.grid.pop(self.bottom)
            self.grid.insert(self.top, self._blank_row())
        elif self.cy > 0:
            self.cy -= 1

    def _erase_cells(self, row, a, b):
        for x in range(max(0, a), min(self.cols, b)):
            self.grid[row][x] = Cell(" ", None, self._attr["bg"])

    def _csi(self, buf, final):
        private = buf.startswith("?")
        nums = [int(x) if x.isdigit() else 0 for x in re.split(r"[;:]", buf.lstrip("?>")) if x != ""] \
            if buf.lstrip("?>") else []
        p = lambda i, d=1: (nums[i] if i < len(nums) and nums[i] > 0 else d)
        self._wrap_pending = False if final in "ABCDHfGdJK" else self._wrap_pending
        if final == "m":
            self._sgr(nums or [0])
        elif final == "A":
            self.cy = max(self.top if self.cy >= self.top else 0, self.cy - p(0))
        elif final == "B":
            self.cy = min(self.bottom if self.cy <= self.bottom else self.rows - 1, self.cy + p(0))
        elif final == "C":
            self.cx = min(self.cols - 1, self.cx + p(0))
        elif final == "D":
            self.cx = max(0, self.cx - p(0))
        elif final in "Hf":
            self.cy, self.cx = min(self.rows - 1, p(0) - 1), min(self.cols - 1, p(1) - 1)
        elif final == "G":
            self.cx = min(self.cols - 1, p(0) - 1)
        elif final == "d":
            self.cy = min(self.rows - 1, p(0) - 1)
        elif final == "E":
            self.cx, self.cy = 0, min(self.rows - 1, self.cy + p(0))
        elif final == "F":
            self.cx, self.cy = 0, max(0, self.cy - p(0))
        elif final == "J":
            mode = nums[0] if nums else 0
            if mode == 0:
                self._erase_cells(self.cy, self.cx, self.cols)
                for y in range(self.cy + 1, self.rows):
                    self._erase_cells(y, 0, self.cols)
            elif mode == 1:
                for y in range(0, self.cy):
                    self._erase_cells(y, 0, self.cols)
                self._erase_cells(self.cy, 0, self.cx + 1)
            else:
                for y in range(self.rows):
                    self._erase_cells(y, 0, self.cols)
        elif final == "K":
            mode = nums[0] if nums else 0
            if mode == 0:
                self._erase_cells(self.cy, self.cx, self.cols)
            elif mode == 1:
                self._erase_cells(self.cy, 0, self.cx + 1)
            else:
                self._erase_cells(self.cy, 0, self.cols)
        elif final == "L":
            for _ in range(p(0)):
                if self.top <= self.cy <= self.bottom:
                    self.grid.pop(self.bottom)
                    self.grid.insert(self.cy, self._blank_row())
        elif final == "M":
            for _ in range(p(0)):
                if self.top <= self.cy <= self.bottom:
                    self.grid.pop(self.cy)
                    self.grid.insert(self.bottom, self._blank_row())
        elif final == "P":
            row = self.grid[self.cy]
            for _ in range(p(0)):
                if self.cx < len(row):
                    row.pop(self.cx)
                    row.append(Cell())
        elif final == "@":
            row = self.grid[self.cy]
            for _ in range(p(0)):
                row.insert(self.cx, Cell())
                row.pop()
        elif final == "X":
            self._erase_cells(self.cy, self.cx, self.cx + p(0))
        elif final == "S":
            for _ in range(p(0)):
                self.grid.pop(self.top)
                self.grid.insert(self.bottom, self._blank_row())
        elif final == "T":
            for _ in range(p(0)):
                self.grid.pop(self.bottom)
                self.grid.insert(self.top, self._blank_row())
        elif final == "r":
            t, b = p(0) - 1, (nums[1] if len(nums) > 1 and nums[1] else self.rows) - 1
            if 0 <= t < b < self.rows:
                self.top, self.bottom = t, b
            self.cx = self.cy = 0
        elif final == "s":
            self._saved = (self.cx, self.cy)
        elif final == "u":
            self.cx, self.cy = self._saved
        elif final == "n" and nums and nums[0] == 6:
            self.responses.append(("\x1b[%d;%dR" % (self.cy + 1, self.cx + 1)).encode())
        elif final == "c" and not private:
            self.responses.append(b"\x1b[?1;2c")
        elif final in "hl":
            on = final == "h"
            for n in nums:
                if private and n in (1049, 47, 1047):
                    self._alt_screen(on)
                elif private and n == 25:
                    self.cur_visible = on
                elif private and n == 1:
                    self.app_cursor = on

    def _alt_screen(self, on):
        if on and self.alt is None:
            self._saved_main = (self.cx, self.cy)
            self.alt = self._blank_grid()
            self.grid = self.alt
            self.top, self.bottom = 0, self.rows - 1
        elif not on and self.alt is not None:
            self.alt = None
            self.grid = self.main
            self.cx, self.cy = getattr(self, "_saved_main", (0, 0))
            self.top, self.bottom = 0, self.rows - 1

    def _sgr(self, nums):
        a, i = self._attr, 0
        while i < len(nums):
            n = nums[i]
            if n == 0:
                a.update(fg=None, bg=None, bold=False, rev=False, ul=False)
            elif n == 1:
                a["bold"] = True
            elif n == 4:
                a["ul"] = True
            elif n == 7:
                a["rev"] = True
            elif n == 22:
                a["bold"] = False
            elif n == 24:
                a["ul"] = False
            elif n == 27:
                a["rev"] = False
            elif 30 <= n <= 37:
                a["fg"] = _PALETTE16[n - 30]
            elif 90 <= n <= 97:
                a["fg"] = _PALETTE16[n - 90 + 8]
            elif 40 <= n <= 47:
                a["bg"] = _PALETTE16[n - 40]
            elif 100 <= n <= 107:
                a["bg"] = _PALETTE16[n - 100 + 8]
            elif n == 39:
                a["fg"] = None
            elif n == 49:
                a["bg"] = None
            elif n in (38, 48) and i + 1 < len(nums):
                key = "fg" if n == 38 else "bg"
                if nums[i + 1] == 5 and i + 2 < len(nums):
                    a[key] = _xterm256(nums[i + 2])
                    i += 2
                elif nums[i + 1] == 2 and i + 4 < len(nums):
                    a[key] = "#%02x%02x%02x" % tuple(min(255, v) for v in nums[i + 2:i + 5])
                    i += 4
            i += 1

    # ---- output
    def text_lines(self):
        return ["".join(c.ch for c in row).rstrip() for row in self.grid]

    def markup(self, default_fg="#d0d7de", default_bg="#0d1117", with_scrollback=0):
        """Render to Kivy markup. Kivy markup has no background color, so reverse
        video and the cursor are shown with a highlight color / block glyph."""
        hi = "#ffd33d"
        rows = (self.scrollback[-with_scrollback:] if with_scrollback and self.grid is self.main else []) + self.grid
        base = len(rows) - self.rows
        out = []
        for y, row in enumerate(rows):
            parts, last, opened = [], None, False
            for x, c in enumerate(row):
                cur = (y - base == self.cy and x == self.cx and self.cur_visible)
                fg, bold = c.fg or default_fg, c.bold
                ch = c.ch
                if cur and ch == " ":
                    ch = "\u2588"
                if c.rev or cur:
                    fg, bold = hi, True
                key = (fg, bold, c.ul)
                if key != last:
                    if opened:
                        parts.append("[/color]")
                    parts.append("[color=%s]" % fg)
                    opened, last = True, key
                ch = "&bl;" if ch == "[" else "&br;" if ch == "]" else "&amp;" if ch == "&" else ch
                parts.append(ch)
            if opened:
                parts.append("[/color]")
            out.append("".join(parts))
        return "\n".join(out)
