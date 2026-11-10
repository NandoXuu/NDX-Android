#!/usr/bin/env python3
"""Validate an ndx-plugin.toml package directory: python tools/validate_plugin.py <dir>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ndx import __version__                                    # noqa: E402
from ndx.package_manager.manifest import MANIFEST_NAME, parse_manifest, validate_manifest  # noqa: E402


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    f = root / MANIFEST_NAME
    if not f.is_file():
        print("error: %s not found" % f)
        return 1
    manifest, problems = validate_manifest(parse_manifest(f.read_text(encoding="utf-8")), __version__)
    if manifest.has_code and not (root / manifest.entry.get("module", "")).is_file():
        problems.append("entry module file is missing")
    for p in problems:
        print("PROBLEM:", p)
    print("%s %s: %s" % (manifest.id, manifest.version, "INVALID" if problems else "valid"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
