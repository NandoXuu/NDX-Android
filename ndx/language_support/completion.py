import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def current_prefix(text, cursor):
    i = cursor
    while i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_"):
        i -= 1
    return text[i:cursor]


def complete(prefix, text, language=None, limit=8):
    """Keyword + buffer-word completion (no semantic analysis; LSP supplies that)."""
    if len(prefix) < 2:
        return []
    pool = []
    if language:
        pool.extend(language.keywords)
    pool.extend(_WORD.findall(text))
    seen, out = set(), []
    low = prefix.lower()
    for w in sorted(set(pool), key=lambda w: (not w.startswith(prefix), len(w), w)):
        if w.lower().startswith(low) and w != prefix and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= limit:
            break
    return out
