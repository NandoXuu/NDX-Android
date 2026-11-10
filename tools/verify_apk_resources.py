#!/usr/bin/env python3
"""Check that a built APK contains the Android resources NDX expects.
usage: python tools/verify_apk_resources.py bin/ndx-*.apk

Verifies (by inspecting the zip): launcher icons in every density bucket, adaptive icon XML,
notification icons, splash, values resources, bundled fonts. It cannot render the icon:
visual checks (masking, splash, themes, densities) must be done on devices/emulators."""
import re
import sys
import zipfile

DENS = ["mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"]


def main(paths):
    if not paths:
        print(__doc__)
        return 2
    code = 0
    for apk in paths:
        names = zipfile.ZipFile(apk).namelist()
        def has(rx):
            return any(re.search(rx, n) for n in names)
        checks = []
        for d in DENS:
            checks.append(("launcher icon mipmap-%s" % d, has(r"^res/mipmap-%s(-v\d+)?/" % d)))
            checks.append(("notification icon drawable-%s" % d, has(r"^res/drawable-%s(-v\d+)?/ic_stat_ndx" % d)))
        checks += [("adaptive icon (anydpi-v26)", has(r"^res/mipmap-anydpi-v26/")),
                   ("resources.arsc", "resources.arsc" in names),
                   ("presplash/splash drawable", has(r"^res/drawable[^/]*/(presplash|ndx_splash)")),
                   ("fonts packaged", has(r"DejaVuSansMono\.ttf")),
                   ("python sources packaged", has(r"ndx/core/app_core\.py|assets/private\.tar|private\.mp3"))]
        print(apk)
        for label, ok in checks:
            print("  [%s] %s" % ("OK" if ok else "MISSING", label))
            code |= 0 if ok else 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
