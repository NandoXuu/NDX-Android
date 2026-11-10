"""GitHub repository + Releases integration (stdlib urllib only)."""
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request

from ..core.errors import ValidationError, NDXError

API = "https://api.github.com"
UA = "NDX-package-manager/1.0"
REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
MAX_DOWNLOAD = 100 * 1024 * 1024


def parse_repo_url(url):
    m = REPO_RE.match(url.strip())
    if not m:
        raise ValidationError("Only https://github.com/<owner>/<repo> URLs are supported")
    return m.group(1), m.group(2)


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get(url, timeout=20, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx())
    except urllib.error.HTTPError as e:
        raise NDXError("GitHub returned HTTP %d for %s" % (e.code, url))
    except (urllib.error.URLError, OSError) as e:
        raise NDXError("Network error (offline?): %s" % e)


def get_json(url):
    with _get(url) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_release(owner, repo, version=None):
    """Return dict(version, url, name, sha256|None, kind). Prefers a release asset
    named *.zip (ndx package); falls back to the source zipball."""
    try:
        rel = get_json("%s/repos/%s/%s/releases/%s" % (
            API, owner, repo, ("tags/v%s" % version) if version else "latest"))
    except NDXError as first:
        if version:
            try:
                rel = get_json("%s/repos/%s/%s/releases/tags/%s" % (API, owner, repo, version))
            except NDXError:
                raise first
        else:
            rel = None
    if rel:
        assets = rel.get("assets", [])
        zips = [a for a in assets if a["name"].endswith(".zip")]
        sums = {a["name"]: a for a in assets if a["name"].endswith(".sha256")}
        if zips:
            a = zips[0]
            digest = None
            if a["name"] + ".sha256" in sums:
                with _get(sums[a["name"] + ".sha256"]["browser_download_url"], accept="*/*") as r:
                    digest = r.read(512).decode().split()[0].lower()
            return {"version": rel["tag_name"].lstrip("v"), "url": a["browser_download_url"],
                    "sha256": digest, "kind": "release-asset"}
        return {"version": rel["tag_name"].lstrip("v"), "url": rel["zipball_url"], "sha256": None,
                "kind": "release-source"}
    repo_info = get_json("%s/repos/%s/%s" % (API, owner, repo))
    branch = repo_info.get("default_branch", "main")
    return {"version": None, "url": "https://github.com/%s/%s/archive/refs/heads/%s.zip" % (owner, repo, branch),
            "sha256": None, "kind": "branch"}


def download(url, dest, expected_sha256=None, max_bytes=MAX_DOWNLOAD, progress=None):
    h, total = hashlib.sha256(), 0
    with _get(url, timeout=60, accept="application/octet-stream") as r, open(dest, "wb") as out:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValidationError("download exceeds the size limit")
            h.update(chunk)
            out.write(chunk)
            if progress:
                progress(total)
    digest = h.hexdigest()
    if expected_sha256 and digest != expected_sha256.lower():
        raise ValidationError("checksum mismatch (expected %s, got %s)" % (expected_sha256, digest))
    return digest


def search_topic(query):
    q = "topic:ndx-plugin %s" % query
    data = get_json("%s/search/repositories?q=%s&per_page=20" % (API, urllib.request.quote(q)))
    return [{"full_name": i["full_name"], "description": i.get("description") or "", "url": i["html_url"],
             "stars": i.get("stargazers_count", 0)} for i in data.get("items", [])]
