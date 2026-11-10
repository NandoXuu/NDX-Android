import time
import unittest

from ndx.terminal import VTScreen, PtySession, encode_key
from ndx.utilities import which


class TestVT(unittest.TestCase):
    def test_basic_text_and_wrap(self):
        v = VTScreen(3, 5)
        v.feed(b"abcdefg")
        self.assertEqual(v.text_lines()[:2], ["abcde", "fg"])

    def test_clear_and_cursor(self):
        v = VTScreen(4, 10)
        v.feed(b"hello\x1b[2J\x1b[1;1Hx\x1b[3;4Hy")
        self.assertEqual(v.text_lines()[0], "x")
        self.assertEqual(v.text_lines()[2], "   y")

    def test_scroll_and_scrollback(self):
        v = VTScreen(2, 5)
        v.feed(b"a\r\nb\r\nc")
        self.assertEqual(v.text_lines(), ["b", "c"])
        self.assertEqual(len(v.scrollback), 1)

    def test_alt_screen_restores(self):
        v = VTScreen(3, 10)
        v.feed(b"main\x1b[?1049h\x1b[2Jalt\x1b[?1049l")
        self.assertEqual(v.text_lines()[0], "main")

    def test_colors_in_markup(self):
        v = VTScreen(2, 10)
        v.feed(b"\x1b[31mred\x1b[0m [x]")
        m = v.markup()
        self.assertIn("[color=#cc0000]", m)
        self.assertIn("&bl;", m)

    def test_cursor_report(self):
        v = VTScreen(5, 10)
        v.feed(b"ab\x1b[6n")
        self.assertEqual(v.responses, [b"\x1b[1;3R"])

    def test_utf8_split(self):
        v = VTScreen(2, 10)
        v.feed("é".encode()[:1])
        v.feed("é".encode()[1:])
        self.assertEqual(v.text_lines()[0], "é")


class TestKeys(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(encode_key("c", ctrl=True), b"\x03")
        self.assertEqual(encode_key("x", alt=True), b"\x1bx")
        self.assertEqual(encode_key("up"), b"\x1b[A")
        self.assertEqual(encode_key("up", app_cursor=True), b"\x1bOA")
        self.assertEqual(encode_key("f5"), b"\x1b[15~")
        self.assertEqual(encode_key("tab", shift=True), b"\x1b[Z")


@unittest.skipUnless(which("sh"), "needs sh")
class TestPty(unittest.TestCase):
    def wait_for(self, s, text, t=8):
        end = time.time() + t
        while time.time() < end:
            if text in "\n".join(s.screen.text_lines()):
                return True
            time.sleep(0.05)
        return False

    def test_real_pty_interactive(self):
        s = PtySession(["sh", "-i"], rows=10, cols=60).start()
        try:
            s.write("tty; stty size\r")
            self.assertTrue(self.wait_for(s, "/dev/"), s.screen.text_lines())     # a real tty
            self.assertTrue(self.wait_for(s, "10 60"), s.screen.text_lines())
            s.resize(12, 70)
            time.sleep(0.2)
            s.write("stty size\r")
            self.assertTrue(self.wait_for(s, "12 70"), s.screen.text_lines())
            s.write(encode_key("d", ctrl=True))                                   # Ctrl-D exits shell
            end = time.time() + 5
            while s.alive() and time.time() < end:
                time.sleep(0.05)
            self.assertFalse(s.alive())
        finally:
            s.terminate()

    def test_ctrl_c_interrupts(self):
        s = PtySession(["sh", "-i"], rows=10, cols=60).start()
        try:
            s.write("sleep 30\r")
            time.sleep(0.5)
            s.write(encode_key("c", ctrl=True))
            time.sleep(0.3)
            s.write("echo alive$((1+1))\r")
            self.assertTrue(self.wait_for(s, "alive2"), s.screen.text_lines())
        finally:
            s.terminate()


if __name__ == "__main__":
    unittest.main()
