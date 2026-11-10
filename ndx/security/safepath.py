"""Path-traversal-safe helpers and a hardened zip extractor."""
import os
import stat
import zipfile
from pathlib import Path

from ..core.errors import ValidationError

MAX_FILES = 5000
MAX_TOTAL = 200 * 1024 * 1024


def safe_join(base, *parts):
    base = Path(base).resolve()
    target = (base.joinpath(*parts)).resolve()
    if target != base and base not in target.parents:
        raise ValidationError("path escapes base directory: %s" % "/".join(parts))
    return target


def safe_extract_zip(zip_path, dest, max_files=MAX_FILES, max_total=MAX_TOTAL):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ValidationError("archive has too many entries (%d)" % len(infos))
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValidationError("symlinks are not allowed in packages: %s" % info.filename)
            target = safe_join(dest, info.filename)
            total += info.file_size
            if total > max_total:
                raise ValidationError("archive expands beyond the size limit")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
            os.chmod(target, 0o644)   # never keep the executable bit from untrusted archives
    return dest
