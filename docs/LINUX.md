# Linux environments guide
NDX uses **PRoot** (userspace chroot). It is **not a virtual machine**: shared Android kernel, no independent kernel,
limited hardware access, no automatic GPU, compatibility restrictions, no VM-grade isolation.

1. **PRoot is installed by NDX**: Environments > "Install PRoot" or `ndx tool install proot` (verified, rolled back on failure).
   `ndx linux install <distro> --install-proot` does it automatically after consent. NDX no longer asks you to copy a binary.
   Install order of the catalog entry: native lib shipped in the APK (none bundled in this build) > download > Termux package > manual file.
2. `ndx linux install <distro>` or Environments > Download / From file. Alpine and Ubuntu archives are verified against the
   upstream-published checksum files; without one the install is refused unless you explicitly allow an unverified download.
   Catalog URLs were **not verified online** in this build (docs/TOOLCHAIN.md).
3. Environment ids are validated and resolved inside the environments root (no `..`, absolute paths or symlink escapes; tested).
4. **Check Linux Environment** (button, or `ndx linux check [id] [--no-start]`): PRoot present/runnable, storage, rootfs
   sanity (shell, /etc, symlink safety), a real startup test, DNS/network note. Exit code 1 on FAIL.
5. Open shell -> PTY session in PRoot. Run/build with the `PRoot: <env>` target (project at `/project`) or `ndx run --env <id> tool`.
6. Extraction is hardened (no device nodes, no writes through symlinks, no path escape). Backup/restore = tar.gz.
PRoot runs with `--kill-on-exit` and `PROOT_TMP_DIR`/`PROOT_LOADER` when a loader is available. Behaviour on real devices
(seccomp, Android 14+ restrictions, performance) is **not tested**. Termux is another app (TERMUX.md).
