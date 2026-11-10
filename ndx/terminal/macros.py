"""Configurable key combinations: 'ctrl+c', 'alt+x', 'f5', 'ctrl+alt+d'."""
from .keys import SPECIAL_KEYS, encode_key


def parse_combo(text):
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combination")
    key, mods = parts[-1], set(parts[:-1])
    if mods - {"ctrl", "alt", "shift"}:
        raise ValueError("unknown modifier in '%s'" % text)
    if key not in SPECIAL_KEYS and len(key) != 1:
        raise ValueError("unknown key '%s'" % key)
    return key, "ctrl" in mods, "alt" in mods, "shift" in mods


def combo_bytes(text):
    key, ctrl, alt, shift = parse_combo(text)
    return encode_key(key, ctrl, alt, shift)
