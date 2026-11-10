import os
import shutil
from pathlib import Path


def dir_size(path, limit_files=200000):
    total, count = 0, 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
            count += 1
            if count > limit_files:
                return total
    return total


def disk_usage(path):
    u = shutil.disk_usage(str(path))
    return {"total": u.total, "used": u.used, "free": u.free}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_text(path, max_bytes=5 * 1024 * 1024):
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValueError("file too large to open in editor (%d bytes)" % size)
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"\x00" in raw[:4096]:
        raise ValueError("binary file")
    return raw.decode("utf-8", errors="replace")
