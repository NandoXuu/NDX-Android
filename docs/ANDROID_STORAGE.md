# Android storage and terminal runtime

NDX keeps mutable data in the app-specific external files directory returned by
`Context.getExternalFilesDir(null)`, under `ndx_home/`. On a typical install this
is:

`/storage/emulated/0/Android/data/org.ndx.ndx/files/ndx_home`

This is app-scoped storage: Android does not require broad `MANAGE_EXTERNAL_STORAGE`
or legacy read/write storage permissions for NDX to access its own directory.
Other apps and the user may not be able to browse it freely on newer Android
versions. Use Android's Storage Access Framework integration when files must be
shared with another app or saved in a user-selected public folder.

The packaged Python modules remain in the application directory. They are not
copied into `ndx_home`. The first startup after upgrading attempts to copy the
previous `ANDROID_PRIVATE/ndx_home` directory into the new location; the old
directory is not deleted. If external app-specific storage is unavailable or
the migration cannot be written, NDX falls back to the previous private path.
`NDX_HOME` remains an explicit override for development and testing.

The terminal's Android PTY sessions prepend the installed NDX source root to
`PYTHONPATH`. This allows child Python processes using the app's bundled
interpreter to import `ndx`; it does not make the package available to an
unrelated `/system/bin/sh` process running under a different UID or interpreter.

## Network

`buildozer.spec` already declares `INTERNET` and `ACCESS_NETWORK_STATE`.
No additional network permission is needed for Python URL downloads. Network
access can still fail because of DNS, connectivity, TLS certificates, server
availability, or Android process restrictions; the permission alone does not
guarantee a successful download.

## PRoot

The Linux manager first resolves the catalog-managed `proot` executable. It
also recognizes common static binary filenames (`proot_static`,
`proot-static`, `proot-static-aarch64`, and related ARM64 names) in NDX's
`bin/` directory and the managed PRoot installation directories. The file
must still be executable and compatible with the device ABI; discovery is not
a substitute for the runtime compatibility check.

## Tests and APK size

The `tests/` directory is excluded from the APK intentionally to reduce size.
Run tests from the source checkout on a desktop/CI environment:

`python -m unittest discover -s tests -t .`

The tests are not expected to run from the installed APK unless a separate
on-device test package is built.
