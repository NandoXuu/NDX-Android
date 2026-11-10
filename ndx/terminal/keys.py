"""Special-key encoding for the terminal keyboard row."""
SPECIAL_KEYS = {
    "esc": "\x1b", "tab": "\t", "enter": "\r", "backspace": "\x7f",
    "up": "\x1b[A", "down": "\x1b[B", "right": "\x1b[C", "left": "\x1b[D",
    "home": "\x1b[H", "end": "\x1b[F", "pgup": "\x1b[5~", "pgdn": "\x1b[6~",
    "ins": "\x1b[2~", "del": "\x1b[3~", "shift-tab": "\x1b[Z",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
    "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
    "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
}


def encode_key(key, ctrl=False, alt=False, shift=False, app_cursor=False):
    """Return the byte sequence for a key. `key` is a name from SPECIAL_KEYS or
    a single character."""
    if key in SPECIAL_KEYS:
        seq = SPECIAL_KEYS[key]
        if app_cursor and key in ("up", "down", "left", "right", "home", "end"):
            seq = "\x1bO" + seq[-1]
        if key == "tab" and shift:
            seq = SPECIAL_KEYS["shift-tab"]
    elif len(key) == 1:
        seq = key
        if ctrl:
            if key == " ":
                seq = "\x00"
            elif key == "?":
                seq = "\x7f"
            elif "@" <= key.upper() <= "_":
                seq = chr(ord(key.upper()) & 0x1F)
            elif key == "-":
                seq = "\x1f"
        elif shift:
            seq = key.upper()
    else:
        return b""
    if alt:
        seq = "\x1b" + seq
    return seq.encode("utf-8")
