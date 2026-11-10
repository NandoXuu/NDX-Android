# Build guide (APK)

> STATUS: the build configuration was written against Buildozer 1.5 / python-for-android (p4a) conventions but **the
> APK was not built or run in the environment that produced this project** (no network, no Android SDK/NDK, no Kivy).
> Treat the first `buildozer android debug` as a verification step and expect to adjust recipe versions.

## Prerequisites (Linux / WSL2 / Docker)
`pip install buildozer cython` + system packages from the Buildozer docs (JDK 17, git, zip, unzip, autoconf, libtool,
pkg-config, zlib, libffi, libssl). First build downloads the SDK/NDK (~3 GB).

## Steps
```
./build.sh          # regenerates assets, runs tests, builds, then verifies the APK's resources
# or manually:
python3 tools/generate_assets.py
buildozer -v android debug
python3 tools/verify_apk_resources.py bin/*.apk
```
Release: configure a keystore (`buildozer android release`, env `P4A_RELEASE_KEYSTORE*`) and sign/zipalign as usual.

## Choices in buildozer.spec
* `requirements`: python3/hostpython3 3.11.5 (needed for `tomllib`), `kivy==2.3.0`, `kivymd==1.2.0` (PyPI release; the UI
  uses the 1.2.0 API: `MDTopAppBar`, `MDRaisedButton`, `MDFlatButton`, `MDCard`...), `pillow`, `pygments`, `pyjnius`,
  `plyer`, `packaging`, `certifi`, `psutil`.
  KivyMD 2.x (master) has a different API and is NOT compatible with this UI.
  `requests` is not used (stdlib `urllib` + `certifi`).
  Verify that p4a has recipes for `pillow`, `psutil`, `pyjnius`; drop `psutil` if it fails: NDX falls back to /proc.
* `android.api = 33`, `minapi = 24`, `ndk = 25b`, archs `arm64-v8a, armeabi-v7a`.
  Raising `api` to 34 requires a foreground-service type and the matching `FOREGROUND_SERVICE_*` permission; not done (untestable here).
* Permissions: `INTERNET, ACCESS_NETWORK_STATE, FOREGROUND_SERVICE, POST_NOTIFICATIONS, WAKE_LOCK` plus the optional `com.termux.permission.RUN_COMMAND`. `android.extra_manifest_xml` adds package visibility for `com.termux`; SAF uses the Android system picker and requires no broad storage permission (see PERMISSIONS.md and TERMUX.md).
* Requirements are split (`requirements/`): the spec `requirements` must equal `requirements/android.txt` (enforced by `tests/test_build_config.py`, which also checks that no module imports an unlisted third-party package at top level). The APK stays small: PRoot, git, python, adb, compilers, rootfs and AI engines are downloaded tools, never bundled.
* Service: `services = ndxkeepalive:service/main.py:foreground:sticky` runs the Supervisor (BACKGROUND.md).
* Resources: `android.add_resources` is **generated** by `tools/generate_assets.py` from `assets/android/res/**`.
* `source.exclude_dirs` removes tests/tools/docs from the APK. `plugins/` (example plugins) are shipped.

## Expected first-build issues
* If `icon.adaptive_*` keys are unknown to your Buildozer, upgrade Buildozer (>= 1.5) or delete those 2 lines.
* If a resource path in `android.add_resources` is rejected, run `python3 tools/generate_assets.py` again and inspect
  the printed list; p4a copies each `source:destination` pair into `res/`.
* The `mipmap-*/icon.png` density files intentionally use p4a's default resource name `icon` so Android picks the right
  density; adaptive layers come from the `icon.adaptive_*` keys. `ndx_launcher.xml` (with monochrome layer) and
  `ndx_ic_*` files are shipped as resources but are not referenced by the generated manifest (see ASSETS.md).

## Static checks (no APK is built by them)
`python -m pytest tests/test_build_config.py` validates the spec/requirements/permissions/resources; `tools/verify_apk_resources.py` inspects a real APK if you build one.

## GitHub Actions
`.github/workflows/android.yml` runs the test suite and builds a debug APK on pushes and pull requests.
A version tag (`v*`) also triggers an APK artifact upload. The workflow does not sign a release APK;
configure signing secrets separately before publishing a production build.
