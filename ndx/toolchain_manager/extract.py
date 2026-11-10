"""Safe archive extraction for downloaded tools (executables are kept, unlike
security.safe_extract_zip which strips the exec bit for untrusted plugin zips)."""
import os
import posixpath
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

from ..core.errors import ValidationError

MAX_FILES = 20000
MAX_TOTAL = 2 * 1024 ** 3


def _clean(name):
    """Normalise an archive member name; reject absolute paths and '..' escapes."""
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    if not n or n == ".":
        return ""
    if n.startswith("/") or ".." in n.split("/") or "\0" in n:
        raise ValidationError("archive entry escapes the destination: %r" % name)
    return posixpath.normpath(n)


def _inside(dest, target):
    try:
        target.resolve().relative_to(dest)
        return True
    except ValueError:
        return False


def _mode(m):
    return 0o755 if (m & 0o111) else 0o644


def extract_archive(archive, kind, dest, member_name="tool", max_files=MAX_FILES, max_total=MAX_TOTAL, cancel=None):
    """Extract `archive` ('raw' | zip | tar[.gz|.xz|.bz2]) into `dest`. Returns the number of files written."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    if kind == "raw":
        shutil.copyfile(archive, dest / member_name)
        os.chmod(dest / member_name, 0o755)
        return 1
    count = total = 0
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise ValidationError("archive has too many entries (%d)" % len(infos))
            for info in infos:
                if cancel is not None and cancel.is_set():
                    raise ValidationError("cancelled")
                name = _clean(info.filename)
                if not name:
                    continue
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    continue                                  # zip symlinks are skipped, never followed
                target = dest / name
                if not _inside(dest, target.parent):
                    raise ValidationError("archive entry escapes the destination: %r" % info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                total += info.file_size
                if total > max_total:
                    raise ValidationError("archive expands beyond the size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 16)
                os.chmod(target, _mode(mode))
                count += 1
        return count
    if kind.startswith("tar"):
        with tarfile.open(archive) as tf:
            for m in tf:
                if cancel is not None and cancel.is_set():
                    raise ValidationError("cancelled")
                if m.isdev() or m.isfifo():
                    continue
                name = _clean(m.name)
                if not name:
                    continue
                target = dest / name
                if not _inside(dest, target.parent):
                    raise ValidationError("archive entry escapes the destination: %r" % m.name)
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if m.issym() or m.islnk():
                    link = m.linkname.replace("\\", "/")
                    resolved = (target.parent / link) if m.issym() else (dest / _clean(link))
                    if m.issym() and (link.startswith("/") or not _inside(dest, resolved)):
                        raise ValidationError("symlink escapes the destination: %r -> %r" % (m.name, m.linkname))
                    if m.islnk() and not _inside(dest, resolved):
                        raise ValidationError("hardlink escapes the destination: %r" % m.name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    if m.issym():
                        os.symlink(link, target)
                    elif resolved.exists():
                        shutil.copyfile(resolved, target)
                        os.chmod(target, _mode(m.mode))
                    count += 1
                    continue
                if not m.isfile():
                    continue
                total += m.size
                if total > max_total or count >= max_files:
                    raise ValidationError("archive expands beyond the size/file-count limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    target.unlink()
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 16)
                os.chmod(target, _mode(m.mode))
                count += 1
        return count
    raise ValidationError("unsupported archive type: %s" % kind)


def guess_kind(path):
    p = str(path).lower()
    for ext, k in ((".tar.gz", "tar.gz"), (".tgz", "tar.gz"), (".tar.xz", "tar.xz"), (".tar.bz2", "tar.bz2"),
                   (".tar", "tar"), (".zip", "zip")):
        if p.endswith(ext):
            return k
    return "raw"
