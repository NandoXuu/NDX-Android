"""ToolDownloader (HTTPS only, host allow-list, size limit, SHA-256, cancel) and ToolCache."""
import hashlib
import os
import re
import shutil
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ..core.errors import NDXError, ValidationError

UA = "NDX-toolchain/1.0"
MAX_DOWNLOAD = 1024 * 1024 * 1024


def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def host_allowed(host, allowed):
    host = (host or "").lower()
    return any(host == a.lower() or host.endswith("." + a.lower()) for a in allowed)


class _GuardRedirect(urllib.request.HTTPRedirectHandler):
    """Every redirect hop must stay on https and on an allowed host."""

    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlparse(newurl)
        if u.scheme != "https" or not host_allowed(u.hostname, self.allowed):
            raise ValidationError("redirect to a host that is not allowed: %s" % (u.hostname or newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ToolDownloader:
    """`opener(url, timeout, allowed_hosts)` can be injected (tests/offline); it returns a file-like
    object with .read(n) and optional .headers."""

    def __init__(self, opener=None):
        self._opener = opener or self._default_open

    @staticmethod
    def _default_open(url, timeout, allowed_hosts):
        op = urllib.request.build_opener(_GuardRedirect(allowed_hosts), urllib.request.HTTPSHandler(context=_ssl_ctx()))
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/octet-stream"})
        return op.open(req, timeout=timeout)

    @staticmethod
    def check_url(url, allowed_hosts):
        u = urlparse(url)
        if u.scheme != "https":
            raise ValidationError("only https downloads are allowed: %s" % url)
        if not host_allowed(u.hostname, allowed_hosts):
            raise ValidationError("host %s is not an allowed download source for this tool" % u.hostname)

    def fetch(self, url, dest, allowed_hosts, max_bytes=MAX_DOWNLOAD, expected_sha256=None, progress=None, cancel=None):
        """Download to `dest` (a *.part file is used and removed on any failure). Returns the sha256 hex digest."""
        self.check_url(url, allowed_hosts)
        part = str(dest) + ".part"
        h, total = hashlib.sha256(), 0
        try:
            resp = self._opener(url, 60, allowed_hosts)
        except urllib.error.HTTPError as e:
            raise NDXError("download failed: HTTP %d from %s" % (e.code, urlparse(url).hostname))
        except (urllib.error.URLError, OSError) as e:
            raise NDXError("download failed (offline?): %s" % getattr(e, "reason", e))
        try:
            length = None
            try:
                length = int(resp.headers.get("Content-Length")) if getattr(resp, "headers", None) else None
            except (TypeError, ValueError):
                length = None
            if length and length > max_bytes:
                raise ValidationError("download is larger than the allowed limit (%d bytes)" % length)
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise NDXError("cancelled")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValidationError("download exceeds the size limit")
                    h.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(total, length)
                out.flush()
                os.fsync(out.fileno())
            if length and total != length:
                raise NDXError("download truncated (%d of %d bytes)" % (total, length))
            digest = h.hexdigest()
            if expected_sha256 and digest != expected_sha256.lower():
                raise ValidationError("checksum mismatch: expected %s, got %s" % (expected_sha256.lower(), digest))
            os.replace(part, dest)
            return digest
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def fetch_sidecar_sha256(self, url, allowed_hosts, filename=None):
        """Read '<hex>  name' style checksum files (or a SHA256SUMS list when `filename` is given)."""
        self.check_url(url, allowed_hosts)
        resp = self._opener(url, 30, allowed_hosts)
        try:
            text = resp.read(256 * 1024).decode("utf-8", "replace")
        finally:
            try:
                resp.close()
            except Exception:
                pass
        for line in text.splitlines():
            m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
            if m and (filename is None or os.path.basename(m.group(2)) == filename):
                return m.group(1).lower()
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s*$", text)
        if m:
            return m.group(1).lower()
        raise ValidationError("could not find a SHA-256 checksum in %s" % url)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ToolCache:
    """Verified downloads keyed by SHA-256 so retries/reinstalls need no re-download."""

    def __init__(self, directory):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256):
        return self.dir / (sha256.lower() + ".dl")

    def get(self, sha256):
        p = self.path_for(sha256)
        if p.is_file() and sha256_file(p) == sha256.lower():
            return p
        if p.exists():
            p.unlink()
        return None

    def put(self, src, sha256):
        dst = self.path_for(sha256)
        shutil.move(str(src), str(dst))
        return dst

    def size(self):
        return sum(f.stat().st_size for f in self.dir.glob("*.dl"))

    def clear(self):
        n = 0
        for f in list(self.dir.glob("*.dl")) + list(self.dir.glob("*.part")):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
        return n

    def trim(self, max_bytes):
        files = sorted(self.dir.glob("*.dl"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > max_bytes:
            f = files.pop(0)
            total -= f.stat().st_size
            f.unlink()
