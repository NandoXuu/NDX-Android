# Downloadable tools (never inside the APK)

Installed on demand by the Toolchain Manager (`ndx tool install <id>`), verified and rolled back on failure.
Sizes/URLs live in `ndx/toolchain_manager/catalog/*.json`.  Upstream URLs and checksums could NOT be verified
offline during development: see docs/TOOLCHAIN.md "Catalog verification".

proot (Android/aarch64/arm), git, python3 (external), node, adb, clangd, rust-analyzer, llama-server, Linux distro
rootfs archives (Alpine / Ubuntu).  Compilers (gcc/clang/rustc/go) live inside Linux environments.
