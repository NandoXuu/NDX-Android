"""Built-in tool catalog and installation profiles.

HONESTY NOTE (read before trusting any entry):
* Android has no official standalone builds of most developer tools. The
  realistic install paths are (1) a Linux (PRoot) environment, installing the
  distro package, (2) Termux packages through the Termux integration, (3) a
  verified download where an upstream publishes Android/static builds, and
  (4) executables bundled in the APK as lib<name>.so (native_lib).
* Download URLs and package names below come from upstream project
  documentation. They were NOT fetched/verified when this catalog was written
  (the build environment had no network). Therefore no entry pins a checksum:
  a download without a pinned checksum is "unverified" and NDX refuses to
  install it unless you supply --sha256 (or explicitly accept the risk).
  To pin a checksum, drop a corrected manifest in
  <ndx_home>/data/toolchain/manifests/<id>.json (it overrides the built-in).
"""
from .manifest import validate_manifest

_PROOT_HOSTS = ["github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"]


def _pk(apt=None, apk=None, pacman=None):
    d = {}
    if apt:
        d["apt"] = apt
    if apk:
        d["apk"] = apk
    if pacman:
        d["pacman"] = pacman
    return {"method": "linux_package", "packages": d}


def _tool(id, name, version, exe, desc, caps, pkgs=None, termux=None, **kw):
    m = {"id": id, "name": name, "version": version, "executable": exe, "description": desc,
         "capabilities": caps, "install": []}
    if pkgs:
        m["install"].append(_pk(**pkgs))
    if termux:
        m["install"].append({"method": "termux_package", "package": termux})
    m.update(kw)
    return m


CATALOG = [
    {   # --- PRoot: needed by the Linux Manager -------------------------------------------------
        "id": "proot", "name": "PRoot", "version": "5.4.0",
        "description": "Userspace chroot/bind/binfmt used to run Linux distributions (not a VM).",
        "platform": ["android", "linux"], "architectures": ["arm64-v8a", "armeabi-v7a"],
        "executable": "bin/proot", "provides": ["proot"], "required": False,
        "capabilities": ["linux_environment"], "version_args": ["--version"],
        "version_regex": r"(\d+\.\d+(?:\.\d+)?)", "allowed_hosts": _PROOT_HOSTS,
        "env": {"PROOT_TMP_DIR": "{tmp}", "PROOT_LOADER": "{dir}/libproot-loader.so",
                "PROOT_LOADER32": "{dir}/libproot-loader32.so"},
        "homepage": "https://github.com/green-green-avk/build-proot-android", "license": "GPL-2.0-or-later",
        "size_estimate": 6 * 1024 * 1024, "source_status": "unverified",
        "notes": "Needs a build made for Android (bionic or static + loader). Desktop-Linux binaries are rejected. "
                 "Packaged as lib<name>.so in the APK it also works on targetSdk>=29 (see docs/TOOLCHAIN.md).",
        "install": [
            {"method": "native_lib", "lib": "libproot.so"},
            {"method": "download", "sources": {
                "arm64-v8a": {"url": "https://raw.githubusercontent.com/green-green-avk/build-proot-android/master/packages/proot-android-aarch64.tar.gz",
                              "archive": "tar.gz", "size": 0, "installed_size": 6 * 1024 * 1024},
                "armeabi-v7a": {"url": "https://raw.githubusercontent.com/green-green-avk/build-proot-android/master/packages/proot-android-armv7a.tar.gz",
                                "archive": "tar.gz", "size": 0, "installed_size": 5 * 1024 * 1024}}},
            {"method": "termux_package", "package": "proot"},
            {"method": "manual"},
        ]},
    _tool("adb", "Android Debug Bridge", "1.0.41", "bin/adb",
          "Official adb client. No Android build exists upstream: install inside a Linux env or Termux and use "
          "Wireless Debugging (localhost).", ["android_debug", "wireless_debugging"],
          pkgs={"apt": "adb", "apk": "android-tools", "pacman": "android-tools"}, termux="android-tools",
          version_args=["version"], version_regex=r"version (\d+\.\d+\.\d+)", requires=[]),
    _tool("git", "Git", "2.43.0", "bin/git", "Distributed version control.", ["vcs"],
          pkgs={"apt": "git", "apk": "git", "pacman": "git"}, termux="git"),
    _tool("clang", "Clang/LLVM", "17.0.0", "bin/clang", "C/C++ compiler.", ["compiler_c", "compiler_cpp"],
          pkgs={"apt": "clang", "apk": "clang", "pacman": "clang"}, termux="clang",
          provides=["clang", "clang++"], size_estimate=300 * 1024 * 1024),
    _tool("gcc", "GCC (gcc and g++)", "13.2.0", "bin/gcc", "GNU C and C++ compiler.", ["compiler_c", "compiler_cpp"],
          pkgs={"apt": "g++", "apk": "g++", "pacman": "gcc"}, provides=["gcc", "g++"],
          size_estimate=250 * 1024 * 1024),
    _tool("jdk", "OpenJDK (javac, java)", "17.0.0", "bin/javac", "Java Development Kit.",
          ["compiler_java", "java_runtime"], pkgs={"apt": "default-jdk-headless", "apk": "openjdk17-jdk",
                                                   "pacman": "jdk-openjdk"},
          provides=["javac", "java"], size_estimate=300 * 1024 * 1024),
    _tool("cmake", "CMake", "3.28.0", "bin/cmake", "Build system generator.", ["build_system"],
          pkgs={"apt": "cmake", "apk": "cmake", "pacman": "cmake"}, termux="cmake"),
    _tool("ninja", "Ninja", "1.11.1", "bin/ninja", "Small build system.", ["build_system"],
          pkgs={"apt": "ninja-build", "apk": "ninja", "pacman": "ninja"}, termux="ninja"),
    _tool("make", "GNU Make", "4.3", "bin/make", "Build automation.", ["build_system"],
          pkgs={"apt": "make", "apk": "make", "pacman": "make"}, termux="make"),
    _tool("python3", "Python 3 (external)", "3.11.0", "bin/python3",
          "External CPython (NOT the interpreter embedded in the APK).", ["python_external"],
          pkgs={"apt": "python3", "apk": "python3", "pacman": "python"}, termux="python", provides=["python3"]),
    _tool("bash", "Bash", "5.2.0", "bin/bash", "GNU Bourne-Again shell.", ["shell"],
          pkgs={"apt": "bash", "apk": "bash", "pacman": "bash"}, termux="bash"),
    _tool("zsh", "Zsh", "5.9", "bin/zsh", "Z shell.", ["shell"],
          pkgs={"apt": "zsh", "apk": "zsh", "pacman": "zsh"}, termux="zsh"),
    _tool("fish", "Fish", "3.7.0", "bin/fish", "Friendly interactive shell.", ["shell"],
          pkgs={"apt": "fish", "apk": "fish", "pacman": "fish"}, termux="fish"),
    _tool("lua", "Lua", "5.4.0", "bin/lua", "Lua interpreter.", ["lua"],
          pkgs={"apt": "lua5.4", "apk": "lua5.4", "pacman": "lua"}, termux="lua54", provides=["lua"]),
    _tool("node", "Node.js", "18.0.0", "bin/node", "JavaScript runtime.", ["javascript"],
          pkgs={"apt": "nodejs", "apk": "nodejs", "pacman": "nodejs"}, termux="nodejs"),
    _tool("rustc", "Rust (rustc + cargo)", "1.70.0", "bin/rustc", "Rust compiler.", ["compiler_rust"],
          pkgs={"apt": "rustc", "apk": "rust", "pacman": "rust"}, termux="rust", provides=["rustc", "cargo"],
          size_estimate=600 * 1024 * 1024),
    _tool("gdb", "GDB", "12.0", "bin/gdb", "GNU debugger.", ["debugger"],
          pkgs={"apt": "gdb", "apk": "gdb", "pacman": "gdb"}, termux="gdb"),
    _tool("curl", "curl", "7.80.0", "bin/curl", "URL transfer tool.", ["network"],
          pkgs={"apt": "curl", "apk": "curl", "pacman": "curl"}, termux="curl"),
    {   # llama.cpp: no verified Android download known; honest install paths only
        "id": "llama.cpp", "name": "llama.cpp (llama-server)", "version": "0.0.1",
        "description": "Local LLM inference server used by the AI runtime.", "platform": ["android", "linux"],
        "architectures": ["arm64-v8a", "x86_64"], "executable": "bin/llama-server",
        "provides": ["llama-server"], "capabilities": ["ai_inference"], "version_args": ["--version"],
        "version_regex": r"(?:version:? ?|b)(\d+)", "size_estimate": 80 * 1024 * 1024,
        "notes": "No verified prebuilt Android binary is catalogued. Use Termux (package llama-cpp), ship it as "
                 "libllama-server.so, or build it with cmake inside a Linux environment.",
        "install": [{"method": "native_lib", "lib": "libllama-server.so"},
                    {"method": "termux_package", "package": "llama-cpp"}, {"method": "manual"}]},
]

# profile -> tool ids (ordered; dependencies are resolved by the installer)
PROFILES = {
    "minimal": {"title": "Minimal", "tools": ["proot", "git"]},
    "python": {"title": "Python Developer", "tools": ["proot", "git", "python3"]},
    "cpp": {"title": "C/C++ Developer", "tools": ["proot", "git", "clang", "cmake", "ninja", "make", "gdb"]},
    "rust": {"title": "Rust Developer", "tools": ["proot", "git", "rustc", "gdb"]},
    "gnu": {"title": "GNU C/C++", "tools": ["proot", "git", "gcc", "make", "gdb"]},
    "java": {"title": "Java Developer", "tools": ["proot", "git", "jdk"]},
    "android": {"title": "Android Developer", "tools": ["proot", "git", "adb"]},
    "linux": {"title": "Linux Developer", "tools": ["proot", "git", "bash", "zsh", "fish", "curl", "make"]},
    "ai": {"title": "AI Developer", "tools": ["proot", "git", "python3", "llama.cpp"]},
    "full": {"title": "Full Developer Environment",
             "tools": ["proot", "git", "python3", "clang", "cmake", "ninja", "make", "gdb", "rustc", "node", "lua",
                       "bash", "zsh", "fish", "curl", "adb", "llama.cpp"]},
}


def builtin_manifests():
    return [validate_manifest(d) for d in CATALOG]
