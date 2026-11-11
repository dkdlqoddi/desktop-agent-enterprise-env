"""Build-time only: the Hermes runtime that the Android app packs into its APK.

    python3 android/hermes/build_runtime.py [--abi x86_64] [--setup-toolchain] [--rebuild NAME] [--frozen]

It writes android/hermes/build/runtime/ (ignored by Git), which android/app/build.gradle.kts packs when it exists:

    assets/hermes/runtime.json         what the app extracts, with the SHA-256 of every archive
    assets/hermes/runtime-common.zip   files that are identical for every ABI: Python code and data
    assets/hermes/runtime-<abi>.zip    the extension modules and other files of one ABI
    jniLibs/<abi>/libhermes_python.so  the Python executable (launcher/hermes_python.c)
    jniLibs/<abi>/libpython3.14.so ... libpython and the libraries its standard extension modules link

An extracted runtime has the desktop bundle's layout (python/, source/, dependencies/, worker.py, manifest.json), so
runtime/hermes/worker.py runs unchanged. Python files are precompiled (unchecked-hash .pyc): the runtime is extracted
once per build and never modified, and the worker runs with -B.

Every download is pinned and checked against its SHA-256: CPython for Android and the C libraries (pins.json), the
Hermes dependencies (runtime/hermes/requirements.txt, the core lock the desktop shares, and requirements-android.txt,
the phone's web search on top), and the build machine's tools (toolchain.txt, build-requirements.txt). Native dependencies without an Android wheel on PyPI are compiled from their sdists with
cibuildwheel; wheels.lock.json records the SHA-256 of every wheel built that way, and only wheels with those hashes
are installed. Builds are not bit-for-bit reproducible, so a rebuilt wheel updates its lock entry (--frozen refuses).

Work files (downloads, the toolchain virtual environment, wheel caches, logs) live in build/android-hermes/.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "android" / "hermes"
WORK = ROOT / "build" / "android-hermes"
OUT = HERE / "build" / "runtime"
SPEC = ROOT / "runtime" / "hermes"
SOURCE = ROOT / "build" / "hermes-source"
LOCK = HERE / "wheels.lock.json"
PINS = json.loads((HERE / "pins.json").read_text(encoding="utf-8"))
DESKTOP = json.loads((SPEC / "manifest.json").read_text(encoding="utf-8"))

PYTHON = PINS["python"]["version"]
PY = ".".join(PYTHON.split(".")[:2])
CP = "cp" + PY.replace(".", "")
API = PINS["python"]["api_level"]
LAUNCHER = "libhermes_python.so"
ABIS = {
    "arm64-v8a": {"triplet": "aarch64-linux-android", "arch": "arm64_v8a", "machine": 183},
    "x86_64": {"triplet": "x86_64-linux-android", "arch": "x86_64", "machine": 62},
}

# Hermes dependencies with native code and no Android wheel on PyPI at the pinned versions, compiled from the sdists
# whose SHA-256 runtime/hermes/requirements.txt pins. A new native dependency makes the PyPI download fail with "no
# matching distribution" until it is listed here.
NATIVE = {
    # libffi from pins.json, linked statically like CPython's _ctypes.
    "cffi": {"pkg_config": True},
    # Linked against the OpenSSL of CPython's Android package (libcrypto_python.so), which the app ships anyway.
    "cryptography": {"openssl": True},
    "jiter": {},
    # libxml2 and libxslt from pins.json, linked statically; no iconv (bionic has none before API 28), no zlib.
    "lxml": {"pkg_config": True, "patch": "patches/lxml-6.1.3-cross-build.patch"},
    # JPEG (libjpeg-turbo, linked statically) and zlib (the system's) are required; no other codec is built.
    "pillow": {
        "pkg_config": True,
        "config_settings": ["jpeg=enable", "zlib=enable", "platform-guessing=disable", "freetype=disable",
                            "lcms=disable", "webp=disable", "jpeg2000=disable", "imagequant=disable", "tiff=disable",
                            "xcb=disable", "avif=disable"],
    },
    # 7.2.2 refuses sys.platform "android" when it builds and when it is imported (see the patch).
    "psutil": {"patch": "patches/psutil-7.2.2-android.patch"},
    # rustls on aws-lc, whose C part aws-lc-sys builds with CMake and the NDK.
    "primp": {"cmake": True},
    "pydantic-core": {},
    "ruamel-yaml-clib": {},
}

# The desktop bundle's selection (scripts/build-hermes.py): every module with its data, the bundled plugins, skills and
# the MCP and plugin catalogs. Hermes' own apps and screens, installers, tests and repository tooling are left out at
# the top level only, so a plugin called web or a skill's assets folder stays.
SOURCE_TOP_IGNORED = {".git", ".github", "tests", "tests-js", "website", "web", "apps", "ui-tui", "native", "evals",
                      "assets", "contributors", "docker", "nix"}
SOURCE_IGNORED = {".git", "node_modules", "__pycache__", "tests"}
# Standard library parts a phone never runs: the test suite, IDLE, Tk, the turtle demos, the pip installer and the
# files for compiling extension modules (config-3.14-<triplet>/).
STDLIB_IGNORED = {"__pycache__", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "site-packages"}
STDLIB_BUILD_FILES = re.compile(r"^config-\d+\.\d+-")
STDLIB_TEST_MODULES = re.compile(r"^(_test|_ctypes_test|_xxtestfuzz|xx)")
# Libraries of CPython's Android package that the launcher and the extension modules load from lib/<abi>.
PYTHON_LIBS = ["libpython" + PY + ".so", "libpython3.so", "libcrypto_python.so", "libssl_python.so",
               "libsqlite3_python.so"]
# NDK system libraries an extension module may link: auditwheel's android_24 policy.
SYSTEM_LIBS = {"libandroid.so", "libc.so", "libcamera2ndk.so", "libdl.so", "libEGL.so", "libGLESv1_CM.so",
               "libGLESv2.so", "libjnigraphics.so", "liblog.so", "libm.so", "libmediandk.so", "libOpenMAXAL.so",
               "libOpenSLES.so", "libvulkan.so", "libz.so"}
# Android 15 devices may use 16 KB memory pages; a library aligned to less does not load there.
PAGE_ALIGN = 16384


class BuildError(Exception):
    pass


def log(message):
    print(message, flush=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def download(url, digest, folder):
    """The pinned file in folder, downloaded once and checked against its SHA-256 every time."""
    if not url.startswith("https://"):
        raise BuildError(f"Not an HTTPS download: {url}")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / urllib.parse.unquote(url.rsplit("/", 1)[-1])
    if target.exists() and sha256_file(target) == digest:
        return target
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as stream:
        shutil.copyfileobj(response, stream, 1 << 20)
    if sha256_file(partial) != digest:
        partial.unlink()
        raise BuildError(f"Checksum mismatch: {url}")
    partial.replace(target)
    return target


def extract_tar(archive, target, skip=()):
    if not hasattr(tarfile, "data_filter"):
        raise BuildError("Python 3.10.12 or later is required (tarfile extraction filters)")
    with tarfile.open(archive) as bundle:
        members = [member for member in bundle.getmembers() if not member.name.startswith(skip)]
        bundle.extractall(target, members=members, filter="data")


def fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def run(args, *, env=None, cwd=None, log_path=None):
    args = [str(arg) for arg in args]
    if log_path is None:
        subprocess.run(args, env=env, cwd=cwd, check=True)
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        result = subprocess.run(args, env=env, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-40:])
        raise BuildError(f"{Path(args[0]).name} failed ({result.returncode}). Last lines of {log_path}:\n{tail}")


def base_env(extra=None):
    """A clean environment: no user Python, pip or compiler settings, and no Windows directories on PATH (WSL)."""
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    env = {
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "PATH": os.pathsep.join([str(cargo_home / "bin"), "/usr/local/bin", "/usr/bin", "/bin"]),
        "TMPDIR": str(WORK / "tmp"),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
    for key in ("CARGO_HOME", "RUSTUP_HOME", "JAVA_HOME", "USER", "LOGNAME", "HTTPS_PROXY", "https_proxy", "NO_PROXY",
                "no_proxy", "SSL_CERT_FILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    (WORK / "tmp").mkdir(parents=True, exist_ok=True)
    env.update(extra or {})
    return env


# Bootstrap (any Python 3.10+): the pinned CPython 3.14.0 for the build machine and the toolchain environment.


def host_python():
    """The desktop's pinned python-build-standalone 3.14.0: the build machine's interpreter and bytecode compiler."""
    archive = download(DESKTOP["linux_url"], DESKTOP["linux_sha256"], ROOT / "build")
    target = WORK / "host-python"
    stamp = target / ".sha256"
    if not stamp.exists() or stamp.read_text() != DESKTOP["linux_sha256"]:
        fresh(target)
        # share/ is terminfo and manual pages, whose hard links Python 3.10's tarfile filter misjudges.
        extract_tar(archive, target, skip=("python/share/",))
        stamp.write_text(DESKTOP["linux_sha256"])
    return target / "python" / "bin" / ("python" + PY)


def toolchain_venv():
    venv = WORK / "toolchain-venv"
    python = venv / "bin" / "python"
    requirements = HERE / "toolchain.txt"
    stamp = venv / ".toolchain-sha256"
    digest = sha256_file(requirements)
    if not python.exists() or not stamp.exists() or stamp.read_text() != digest:
        shutil.rmtree(venv, ignore_errors=True)
        run([host_python(), "-I", "-m", "venv", venv], env=base_env())
        run([python, "-I", "-m", "pip", "install", "--quiet", "--require-hashes", "--no-deps", "--only-binary=:all:",
             "-r", requirements], env=base_env())
        stamp.write_text(digest)
    return python


# Toolchain: Android SDK packages and Rust.


def android_sdk():
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(key):
            return Path(os.environ[key])
    properties = ROOT / "android" / "local.properties"
    if properties.exists():
        for line in properties.read_text(encoding="utf-8").splitlines():
            if line.startswith("sdk.dir="):
                return Path(line.split("=", 1)[1].strip())
    raise BuildError("Android SDK not found: set ANDROID_HOME or sdk.dir in android/local.properties")


class Toolchain:
    def __init__(self):
        self.sdk = android_sdk()
        self.ndk = self.sdk / "ndk" / PINS["ndk"]
        self.llvm = self.ndk / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64"
        self.cmake = self.sdk / "cmake" / PINS["cmake"] / "bin" / "cmake"
        self.ninja = self.cmake.with_name("ninja")
        self.cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
        self.rustup = self.cargo_home / "bin" / "rustup"

    def clang(self, abi):
        return self.llvm / "bin" / f"{ABIS[abi]['triplet']}{API}-clang"

    def missing(self, abis):
        problems = []
        if not self.clang(next(iter(abis))).exists():
            problems.append(f"NDK {PINS['ndk']} ({self.ndk})")
        if not self.cmake.exists():
            problems.append(f"CMake {PINS['cmake']} ({self.cmake.parent.parent})")
        rust = PINS["rust"]["toolchain"]
        if not self.rustup.exists():
            problems.append(f"rustup ({self.rustup})")
        else:
            env = base_env({"RUSTUP_TOOLCHAIN": rust, "RUSTUP_AUTO_INSTALL": "0"})
            version = subprocess.run([self.rustup, "run", rust, "rustc", "--version"], env=env, capture_output=True,
                                     text=True, check=False)
            if version.returncode or not version.stdout.startswith(f"rustc {rust} "):
                problems.append(f"Rust {rust}")
            else:
                targets = subprocess.run([self.rustup, "target", "list", "--installed", "--toolchain", rust], env=env,
                                         capture_output=True, text=True, check=False).stdout.split()
                problems += [f"Rust target {ABIS[abi]['triplet']}" for abi in abis
                             if ABIS[abi]["triplet"] not in targets]
        return problems

    def setup(self, abis):
        """Installs the pinned NDK and CMake with sdkmanager and the pinned Rust with rustup, for this user only."""
        sdkmanager = self.sdk / "cmdline-tools" / "latest" / "bin" / "sdkmanager"
        if not self.clang(next(iter(abis))).exists() or not self.cmake.exists():
            log(f"Installing NDK {PINS['ndk']} and CMake {PINS['cmake']} with sdkmanager...")
            run([sdkmanager, "--install", f"ndk;{PINS['ndk']}", f"cmake;{PINS['cmake']}"],
                env=base_env({"ANDROID_HOME": str(self.sdk)}))
        rust = PINS["rust"]
        targets = [arg for abi in abis for arg in ("--target", ABIS[abi]["triplet"])]
        if not self.rustup.exists():
            installer = download(rust["rustup_init_url"], rust["rustup_init_sha256"], WORK / "downloads" / "rustup")
            installer.chmod(0o755)
            log(f"Installing rustup {rust['rustup']} and Rust {rust['toolchain']}...")
            run([installer, "-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", rust["toolchain"],
                 *targets], env=base_env())
        else:
            run([self.rustup, "toolchain", "install", rust["toolchain"], "--profile", "minimal", *targets],
                env=base_env())


# Inputs: the Hermes source, the requirements, CPython for Android, the native libraries.


def hermes_source():
    """Checks build/hermes-source like the desktop builder; returns its commit time (SOURCE_DATE_EPOCH of the wheels)."""
    if not SOURCE.exists():
        raise BuildError(f"Hermes source not found at {SOURCE}: run npm run build:hermes once, or clone tag "
                         f"v{DESKTOP['version']} of {DESKTOP['repository']} there")
    commit = subprocess.check_output(["git", "-C", SOURCE, "rev-parse", "HEAD"], text=True).strip()
    if commit != DESKTOP["commit"]:
        raise BuildError("Hermes source commit differs from runtime/hermes/manifest.json")
    if subprocess.check_output(["git", "-C", SOURCE, "status", "--porcelain", "--untracked-files=all"]):
        raise BuildError("Hermes source checkout is modified")
    return int(subprocess.check_output(["git", "-C", SOURCE, "log", "-1", "--format=%ct"], text=True).strip())


REQUIREMENTS = (SPEC / "requirements.txt", HERE / "requirements-android.txt")


def read_requirements():
    """The core lock and the phone's extras: canonical name -> (name, version, marker, hashes)."""
    entries = {}
    for path in REQUIREMENTS:
        for name, entry in read_lock_file(path).items():
            if name in entries and entries[name][1] != entry[1]:
                raise BuildError(f"{path.name} pins {name} {entry[1]}, the core lock {entries[name][1]}")
            entries.setdefault(name, entry)
    return entries


def read_lock_file(path):
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    pattern = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(\S+)\s*(?:;\s*(.*?))?\s*"
                         r"((?:--hash=sha256:[0-9a-f]{64}\s*)+)$")
    entries = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise BuildError(f"Unexpected line in {path.name}: {line[:80]}")
        name, version, marker, hashes = match.groups()
        entries[canonical(name)] = (name, version, marker, re.findall(r"sha256:([0-9a-f]{64})", hashes))
    return entries


def applies(marker, abi):
    """Evaluates a requirement marker for CPython on Android (PEP 738), not for the build machine."""
    if not marker:
        return True
    from packaging.markers import Marker

    machine = ABIS[abi]["triplet"].split("-")[0]
    return Marker(marker).evaluate({
        "implementation_name": "cpython", "implementation_version": PYTHON, "os_name": "posix",
        "platform_machine": machine, "platform_python_implementation": "CPython", "platform_release": "",
        "platform_system": "Android", "platform_version": "", "python_full_version": PYTHON, "python_version": PY,
        "sys_platform": "android",
    })


def python_package(abi):
    """CPython's Android package of an ABI, extracted: prefix/ has libpython, the standard library and headers."""
    pin = PINS["python"]["packages"][abi]
    archive = download(pin["url"], pin["sha256"], WORK / "downloads" / "python")
    # cibuildwheel reads the same verified file from its cache instead of downloading it again.
    cache = WORK / "cibw-cache" / archive.name
    if not cache.exists() or sha256_file(cache) != pin["sha256"]:
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, cache)
    target = WORK / "python" / ABIS[abi]["triplet"]
    stamp = target / ".sha256"
    if not stamp.exists() or stamp.read_text() != pin["sha256"]:
        fresh(target)
        extract_tar(archive, target)
        stamp.write_text(pin["sha256"])
    return target / "prefix"


def native_libraries(abi, tools):
    """A prefix with libffi, zlib, libjpeg-turbo, libxml2 and libxslt for pkg-config: what cffi, Pillow and lxml
    build against."""
    triplet = ABIS[abi]["triplet"]
    jpeg = PINS["libjpeg_turbo"]
    ffi = PINS["libffi"]["packages"][abi]
    prefix = WORK / "deps" / triplet
    inputs = json.dumps([ffi["sha256"], jpeg["sha256"], PINS["libxml2"]["sha256"], PINS["libxslt"]["sha256"],
                         PINS["ndk"], PINS["cmake"], API, LIBRARIES_VERSION])
    stamp = prefix / ".inputs"
    if stamp.exists() and stamp.read_text() == inputs:
        return prefix
    fresh(prefix)
    extract_tar(download(ffi["url"], ffi["sha256"], WORK / "downloads" / "deps"), prefix)

    # zlib is an Android system library: the NDK's header and link stub, described for pkg-config.
    sysroot = tools.llvm / "sysroot" / "usr"
    for header in ("zlib.h", "zconf.h"):
        shutil.copy2(sysroot / "include" / header, prefix / "include" / header)
    shutil.copy2(sysroot / "lib" / triplet / str(API) / "libz.so", prefix / "lib" / "libz.so")
    version = re.search(r'#define ZLIB_VERSION "([^"]+)"', (sysroot / "include" / "zlib.h").read_text()).group(1)
    (prefix / "lib" / "pkgconfig" / "zlib.pc").write_text(
        f"prefix={prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n\n"
        f"Name: zlib\nDescription: zlib (Android system library)\nVersion: {version}\n"
        "Libs: -L${libdir} -lz\nCflags: -I${includedir}\n")

    arm = abi == "arm64-v8a"
    cmake_library(tools, abi, prefix, "libjpeg-turbo", jpeg, [
        *([f"-DCMAKE_ASM_FLAGS=--target={triplet}{API}"] if arm else []),
        "-DENABLE_SHARED=OFF", "-DENABLE_STATIC=ON", "-DWITH_TURBOJPEG=OFF", "-DWITH_TOOLS=OFF",
        # The regression tests run on the build target, which is not the build machine.
        "-DWITH_TESTS=OFF",
        # The x86-64 SIMD code needs NASM, which the build machine does not have; that ABI is for the emulator.
        f"-DWITH_SIMD={1 if arm else 0}",
    ])
    # What lxml uses of them (HTML and XML parsing, XPath, XSLT); no iconv, zlib, ICU, modules, programs or tests.
    cmake_library(tools, abi, prefix, "libxml2", PINS["libxml2"], [
        "-DBUILD_SHARED_LIBS=OFF", "-DLIBXML2_WITH_ICONV=OFF", "-DLIBXML2_WITH_ICU=OFF", "-DLIBXML2_WITH_LZMA=OFF",
        "-DLIBXML2_WITH_ZLIB=OFF", "-DLIBXML2_WITH_HTTP=OFF", "-DLIBXML2_WITH_MODULES=OFF",
        "-DLIBXML2_WITH_PYTHON=OFF", "-DLIBXML2_WITH_PROGRAMS=OFF", "-DLIBXML2_WITH_TESTS=OFF",
        # The default XML catalog is compiled in from this directory: the usual one, not the build prefix.
        "-DCMAKE_INSTALL_SYSCONFDIR=/etc",
    ])
    cmake_library(tools, abi, prefix, "libxslt", PINS["libxslt"], [
        # The NDK's toolchain file keeps find_package inside the sysroot: point it at the libxml2 just installed.
        "-DBUILD_SHARED_LIBS=OFF", f"-DLibXml2_DIR={prefix / 'lib' / 'cmake' / ('libxml2-' + PINS['libxml2']['version'])}",
        "-DLIBXSLT_WITH_CRYPTO=OFF",
        # lxml uses the profiler interface (XSLT profile=True), so it stays.
        "-DLIBXSLT_WITH_MODULES=OFF", "-DLIBXSLT_WITH_PROGRAMS=OFF",
        "-DLIBXSLT_WITH_PYTHON=OFF", "-DLIBXSLT_WITH_TESTS=OFF",
    ])
    stamp.write_text(inputs)
    return prefix


def cmake_library(tools, abi, prefix, name, pin, options):
    """A pinned C library built with CMake and the NDK's toolchain file, static and position independent."""
    source = WORK / "src" / f"{name}-{pin['version']}"
    if not (source / "CMakeLists.txt").exists():
        shutil.rmtree(source, ignore_errors=True)
        extract_tar(download(pin["url"], pin["sha256"], WORK / "downloads" / "deps"), source.parent)
    build = fresh(WORK / "deps-build" / f"{name}-{ABIS[abi]['triplet']}")
    run([tools.cmake, "-G", "Ninja", "-S", source, "-B", build, f"-DCMAKE_MAKE_PROGRAM={tools.ninja}",
         f"-DCMAKE_TOOLCHAIN_FILE={tools.ndk / 'build' / 'cmake' / 'android.toolchain.cmake'}",
         f"-DANDROID_ABI={abi}", f"-DANDROID_PLATFORM=android-{API}", "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON",
         "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={prefix}", "-DCMAKE_INSTALL_LIBDIR=lib",
         "-DCMAKE_INSTALL_INCLUDEDIR=include", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
         # No debug information (the NDK's toolchain file adds -g): it would carry the build paths into the modules.
         f"-DCMAKE_C_FLAGS=-g0 -ffile-prefix-map={source}={name} -ffile-prefix-map={build}=build", *options],
        env=base_env(), log_path=WORK / "logs" / f"{name}-{abi}.log")
    run([tools.cmake, "--build", build], env=base_env(), log_path=WORK / "logs" / f"{name}-{abi}-build.log")
    run([tools.cmake, "--install", build], env=base_env(), log_path=WORK / "logs" / f"{name}-{abi}-install.log")


def hashed_requirements(path):
    """(name, version, hashes) of every requirement of a hash-locked requirements file."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    found = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)[^\n]*?((?:--hash=sha256:[0-9a-f]{64}\s*)+)", text, re.MULTILINE)
    return [(name, version, re.findall(r"sha256:([0-9a-f]{64})", hashes)) for name, version, hashes in found]


def downloaded(requirements, folder):
    """Whether folder has a file for every requirement already (a rebuild then needs no network)."""
    digests = {sha256_file(path) for path in folder.glob("*.whl")} if folder.exists() else set()
    return all(any(digest in digests for digest in hashes) for _, _, hashes in requirements)


def build_wheelhouse(python):
    """The build machine's tools and build backends, by hash: the only index the wheel builds may use."""
    target = WORK / "wheelhouse" / "build"
    if downloaded(hashed_requirements(HERE / "build-requirements.txt"), target):
        return target
    run([python, "-I", "-m", "pip", "download", "--quiet", "--require-hashes", "--no-deps", "--only-binary=:all:",
         "--python-version", PY, "--implementation", "cp", "--abi", CP,
         *[arg for tag in ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "linux_x86_64")
           for arg in ("--platform", tag)],
         "-d", target, "-r", HERE / "build-requirements.txt"], env=base_env())
    return target


def sdist(name, version, hashes):
    """The sdist runtime/hermes/requirements.txt pins, from PyPI (or from an earlier download)."""
    folder = WORK / "downloads" / "sdist"
    for path in sorted(folder.glob("*.tar.gz")) if folder.exists() else []:
        if canonical(path.name).startswith(canonical(f"{name}-{version}") + "-") and sha256_file(path) in hashes:
            return path
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
        files = json.load(response)["urls"]
    for file in files:
        if file["packagetype"] == "sdist" and file["digests"]["sha256"] in hashes:
            return download(file["url"], file["digests"]["sha256"], WORK / "downloads" / "sdist")
    raise BuildError(f"No sdist of {name} {version} matches its requirements file")


# Native wheels.


def build_inputs(name, version, archive, abi):
    """Everything a native wheel is built from, so a changed input rebuilds it."""
    options = NATIVE[name]
    inputs = {
        "version": version, "sdist": sha256_file(archive), "python": PINS["python"]["packages"][abi]["sha256"],
        "api_level": API, "ndk": PINS["ndk"], "rust": PINS["rust"]["toolchain"],
        "toolchain": sha256_file(HERE / "toolchain.txt"), "build": sha256_file(HERE / "build-requirements.txt"),
        "options": options, "script": BUILD_SCRIPT_VERSION,
    }
    if options.get("patch"):
        inputs["patch"] = sha256_file(HERE / options["patch"])
    if options.get("pkg_config"):
        inputs["libraries"] = [PINS["libffi"]["packages"][abi]["sha256"], PINS["libjpeg_turbo"]["sha256"],
                               PINS["libxml2"]["sha256"], PINS["libxslt"]["sha256"], LIBRARIES_VERSION]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


# Bumped when the way wheels (or the native libraries they link) are built changes.
BUILD_SCRIPT_VERSION = 2
LIBRARIES_VERSION = 5


def compile_wheel(name, archive, abi, tools, libraries, prefix, epoch):
    """One wheel built by cibuildwheel (--platform android) against the pinned CPython for Android."""
    options = NATIVE[name]
    source = archive
    if options.get("patch"):
        source = fresh(WORK / "src" / f"{name}-{abi}")
        extract_tar(archive, source)
        (source,) = source.iterdir()
        run(["patch", "-p1", "--forward", "--batch", "--quiet", "-i", HERE / options["patch"]], cwd=source)
    output = fresh(WORK / "built" / f"{name}-{abi}")
    temp = fresh(WORK / "tmp" / f"{name}-{abi}")
    rust = ABIS[abi]["triplet"].upper().replace("-", "_")
    rustflags = [
        f"-C link-arg=-Wl,-z,max-page-size={PAGE_ALIGN}",
        # Panic messages carry source paths: keep the builder's home directory out of the binaries.
        f"--remap-path-prefix={tools.cargo_home}=/cargo",
        f"--remap-path-prefix={WORK}=/build",
        # PyO3 links libpython on Android for version-specific builds but not for abi3 ones (cryptography), and a
        # module that does not link it cannot load there. A second -l of the same library changes nothing.
        f"-C link-arg=-L{prefix / 'lib'}",
        f"-C link-arg=-lpython{PY}",
    ]
    env = base_env({
        "TMPDIR": str(temp),
        "ANDROID_HOME": str(tools.sdk),
        "ANDROID_API_LEVEL": str(API),
        "CIBW_CACHE_PATH": str(WORK / "cibw-cache"),
        "CIBW_BUILD": f"{CP}-android_{ABIS[abi]['arch']}",
        "CIBW_BUILD_FRONTEND": "build",
        "CIBW_BUILD_VERBOSITY": "1",
        "CIBW_TEST_SKIP": "*",
        # Every pip install of cibuildwheel and of the sdist's build requirements comes from the hashed wheelhouse.
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(WORK / "wheelhouse" / "build"),
        "PIP_ONLY_BINARY": ":all:",
        "SOURCE_DATE_EPOCH": str(epoch),
        # Crates as the sdist's Cargo.lock pins them (Cargo checks their SHA-256).
        "MATURIN_PEP517_ARGS": "--locked",
        "RUSTUP_TOOLCHAIN": PINS["rust"]["toolchain"],
        f"CARGO_TARGET_{rust}_RUSTFLAGS": " ".join(rustflags),
    })
    if options.get("pkg_config"):
        env["PKG_CONFIG_PATH"] = str(libraries / "lib" / "pkgconfig")
    if options.get("openssl"):
        env.update({
            "OPENSSL_DIR": str(prefix),
            "OPENSSL_NO_VENDOR": "1",
            # cryptography's cffi bridge derives Python's include directory from PYO3_CROSS_LIB_DIR, which
            # cibuildwheel sets to prefix/lib for Android, and misses Python.h; the compiler finds it here instead.
            "C_INCLUDE_PATH": str(prefix / "include" / f"python{PY}"),
        })
    if options.get("config_settings"):
        env["CIBW_CONFIG_SETTINGS"] = " ".join(options["config_settings"])
    if options.get("cmake"):
        env.update({
            "CMAKE": str(tools.cmake),
            "PATH": os.pathsep.join([str(tools.cmake.parent), env["PATH"]]),
            "ANDROID_NDK_ROOT": str(tools.ndk),
            "ANDROID_NDK_HOME": str(tools.ndk),
        })
    # The sdist's own [tool.cibuildwheel] settings (Pillow's before-all script, test commands) are for its CI;
    # this empty configuration replaces them, so only the settings above apply.
    config = WORK / "cibuildwheel.toml"
    config.write_text("# Intentionally empty: android/hermes/build_runtime.py configures cibuildwheel.\n")
    run([sys.executable, "-I", Path(__file__).resolve(), "--cibuildwheel", "--platform", "android", "--archs",
         ABIS[abi]["arch"], "--config-file", config, "--output-dir", output, source], env=env,
        cwd=source if source.is_dir() else temp, log_path=WORK / "logs" / f"{name}-{abi}.log")
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"cibuildwheel produced {len(wheels)} wheels for {name} ({abi})")
    shutil.rmtree(temp, ignore_errors=True)
    return wheels[0]


def native_wheels(abis, requirements, tools, prefixes, epoch, rebuild, frozen):
    """The native wheels for each ABI, from the cache when the lock's hash matches, otherwise built."""
    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    locked = lock.setdefault("wheels", {})
    cache = WORK / "wheelhouse" / "android"
    cache.mkdir(parents=True, exist_ok=True)
    selected = {abi: {} for abi in abis}
    jobs = [(name, abi) for name in NATIVE if name in requirements for abi in abis
            if applies(requirements[name][2], abi)]
    for index, (name, abi) in enumerate(jobs, 1):
        project, version, _, hashes = requirements[name]
        archive = sdist(project, version, hashes)
        inputs = build_inputs(name, version, archive, abi)
        entry = locked.get(name, {}).get("abis", {}).get(abi)
        cached = cache / entry["file"] if entry else None
        if (name not in rebuild and entry and entry["inputs"] == inputs and cached.exists()
                and sha256_file(cached) == entry["sha256"]):
            log(f"[{index}/{len(jobs)}] {name} {version} ({abi}): locked wheel")
            selected[abi][name] = entry
            continue
        if frozen:
            raise BuildError(f"{name} ({abi}) has no cached wheel matching wheels.lock.json (--frozen)")
        started = time.monotonic()
        log(f"[{index}/{len(jobs)}] {name} {version} ({abi}): building...")
        libraries = native_libraries(abi, tools) if NATIVE[name].get("pkg_config") else None
        wheel = compile_wheel(name, archive, abi, tools, libraries, prefixes[abi], epoch)
        digest = sha256_file(wheel)
        shutil.move(wheel, cache / wheel.name)
        if entry and entry["inputs"] == inputs and entry["sha256"] != digest:
            log("    rebuilt with the same inputs; wheel builds are not bit-for-bit reproducible, so the lock changes")
        entry = {"file": wheel.name, "sha256": digest, "inputs": inputs}
        locked.setdefault(name, {})["version"] = version
        locked[name].setdefault("abis", {})[abi] = entry
        selected[abi][name] = entry
        log(f"    {wheel.name} in {time.monotonic() - started:.0f} s")
        write_lock(lock)
    write_lock(lock)
    return selected


def write_lock(lock):
    comment = ("SHA-256 of the Android wheels android/hermes/build_runtime.py compiled; only these are installed into "
               "the runtime. inputs identifies what a wheel was built from.")
    ordered = {"comment": comment, "python": PYTHON, "api_level": API, "wheels": {
        name: {"version": entry["version"], "abis": dict(sorted(entry["abis"].items()))}
        for name, entry in sorted(lock["wheels"].items())}}
    LOCK.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


# The runtime tree of an ABI.


def platform_args(abi):
    return [arg for level in range(API, 20, -1) for arg in ("--platform", f"android_{level}_{ABIS[abi]['arch']}")]


def install_dependencies(abi, requirements, natives, target):
    """runtime/hermes/requirements.txt for Android: PyPI wheels by their pinned hashes, native ones by the lock."""
    requests = WORK / "requirements"
    requests.mkdir(parents=True, exist_ok=True)
    pypi, built = [], []
    for name, (project, version, marker, hashes) in sorted(requirements.items()):
        if not applies(marker, abi):
            continue
        if name in NATIVE:
            built.append(f"{project}=={version} --hash=sha256:{natives[name]['sha256']}")
        else:
            pypi.append(f"{project}=={version} " + " ".join(f"--hash=sha256:{digest}" for digest in hashes))
    common = ["--require-hashes", "--no-deps", "--only-binary=:all:", "--python-version", PY, "--implementation", "cp",
              "--abi", CP, *platform_args(abi)]
    (requests / f"pypi-{abi}.txt").write_text("\n".join(pypi) + "\n")
    (requests / f"all-{abi}.txt").write_text("\n".join(pypi + built) + "\n")
    wheelhouse = WORK / "wheelhouse" / f"pypi-{abi}"
    if not downloaded(hashed_requirements(requests / f"pypi-{abi}.txt"), wheelhouse):
        run([sys.executable, "-I", "-m", "pip", "download", "--quiet", *common, "-d", wheelhouse,
             "-r", requests / f"pypi-{abi}.txt"], env=base_env())
    run([sys.executable, "-I", "-m", "pip", "install", "--quiet", *common, "--no-index", "--no-compile",
         "--find-links", wheelhouse, "--find-links", WORK / "wheelhouse" / "android",
         "--target", target, "-r", requests / f"all-{abi}.txt"], env=base_env())
    # Console scripts point at the build machine's Python, and maturin's SBOMs name its build directories.
    shutil.rmtree(target / "bin", ignore_errors=True)
    for sboms in target.glob("*.dist-info/sboms"):
        shutil.rmtree(sboms)


def stage(abi, requirements, natives, prefix, worker_sha256):
    root = fresh(WORK / "stage" / abi)
    stdlib = prefix / "lib" / f"python{PY}"

    def stdlib_ignored(directory, names):
        dynload = Path(directory).name == "lib-dynload"
        top = Path(directory) == stdlib
        return [n for n in names if n in STDLIB_IGNORED or (dynload and STDLIB_TEST_MODULES.match(n))
                or (top and STDLIB_BUILD_FILES.match(n))]

    shutil.copytree(stdlib, root / "python" / "lib" / f"python{PY}", ignore=stdlib_ignored)
    def source_ignored(directory, names):
        top = Path(directory) == SOURCE
        return [n for n in names if n in SOURCE_IGNORED or (top and n in SOURCE_TOP_IGNORED)]

    shutil.copytree(SOURCE, root / "source", ignore=source_ignored)
    install_dependencies(abi, requirements, natives, root / "dependencies")
    for name in ("worker.py", "requirements.txt"):
        shutil.copy2(SPEC / name, root / name)
    manifest = {"version": DESKTOP["version"], "repository": DESKTOP["repository"], "commit": DESKTOP["commit"],
                "python": PYTHON, "platform": "android", "abi": abi, "api_level": API, "executable": LAUNCHER,
                "worker_sha256": worker_sha256}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


COMPILE = """
import json, py_compile, sys
failed = 0
for source, display in json.load(sys.stdin):
    try:
        # display: tracebacks name the runtime's relative path, never the build machine's.
        py_compile.compile(source, dfile=display, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    except py_compile.PyCompileError:
        failed += 1
print(failed)
"""
# The trees whose modules get bytecode.
BYTECODE_FOLDERS = (f"python/lib/python{PY}", "source", "dependencies")


def compile_bytecode(stages):
    """Unchecked-hash .pyc next to every module, so Python compiles nothing when the worker starts (-B).

    Marshal output depends on the hash seed and on what the compiling process did before (reference flags follow
    reference counts), so the jobs run in fixed shards, each in a fresh interpreter with a fixed seed, and a module
    that is the same for every ABI is compiled once and copied. The same sources then give the same archives, and
    the app extracts again only when something changed.
    """
    sources = {}
    for abi, root in stages.items():
        sources[abi] = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).digest()
                        for folder in BYTECODE_FOLDERS for path in (root / folder).rglob("*.py")}
    first, *others = stages
    shared = {name for name, digest in sources[first].items()
              if all(sources[abi].get(name) == digest for abi in others)} if others else set()
    jobs = [(stages[first], name) for name in sorted(shared)]
    jobs += [(stages[abi], name) for abi in stages for name in sorted(sources[abi]) if name not in shared]
    size = max(1, -(-len(jobs) // (os.cpu_count() or 4)))
    shards = [jobs[index:index + size] for index in range(0, len(jobs), size)]

    def compile_shard(shard):
        # -I would ignore PYTHONHASHSEED: -s -P and a clean environment isolate the compiler instead.
        result = subprocess.run([sys.executable, "-s", "-P", "-c", COMPILE], env=base_env({"PYTHONHASHSEED": "0"}),
                                input=json.dumps([[str(root / name), name] for root, name in shard]),
                                capture_output=True, text=True, check=True)
        return int(result.stdout)

    with concurrent.futures.ThreadPoolExecutor(len(shards)) as pool:
        failed = sum(pool.map(compile_shard, shards))
    for abi in others:
        for name in shared:
            cached = Path(importlib.util.cache_from_source(str(stages[first] / name)))
            if cached.exists():
                target = Path(importlib.util.cache_from_source(str(stages[abi] / name)))
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(cached, target)
    if failed:
        # Templates and data with a .py suffix; Python compiles those (if ever) when they are imported.
        log(f"    {failed} .py files are not valid Python {PY} and have no bytecode")


def launcher(abi, tools, prefix, target):
    run([tools.clang(abi), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie",
         "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", f"-I{prefix / 'include' / f'python{PY}'}",
         HERE / "launcher" / "hermes_python.c", f"-L{prefix / 'lib'}", f"-lpython{PY}",
         # libpython is next to the executable: found even where LD_LIBRARY_PATH is not applied.
         "-Wl,-rpath,$ORIGIN", "-Wl,--build-id=sha1",
         f"-Wl,-z,max-page-size={PAGE_ALIGN}", "-Wl,-z,relro", "-Wl,-z,now", "-Wl,--no-undefined", "-o", target],
        env=base_env())
    run([tools.llvm / "bin" / "llvm-strip", "--strip-unneeded", target], env=base_env())


# Checks and packing.


def elf(path):
    """(e_machine, smallest PT_LOAD alignment, DT_NEEDED, uses Python's C API) of a 64-bit LE ELF file, or None."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        return None
    if data[4:6] != b"\x02\x01":
        raise BuildError(f"Not a 64-bit little-endian ELF file: {path}")
    machine = struct.unpack_from("<H", data, 18)[0]
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    loads, dynamic = [], None
    for index in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, align = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phentsize)
        if kind == 1:
            loads.append((offset, vaddr, filesz, align))
        elif kind == 2:
            dynamic = (offset, filesz)
    needed = []
    if dynamic:
        entries = []
        for offset in range(dynamic[0], dynamic[0] + dynamic[1], 16):
            tag, value = struct.unpack_from("<qQ", data, offset)
            if tag == 0:
                break
            entries.append((tag, value))
        strtab = next(value for tag, value in entries if tag == 5)
        base = next(offset + strtab - vaddr for offset, vaddr, filesz, _ in loads if vaddr <= strtab < vaddr + filesz)
        for tag, value in entries:
            if tag == 1:
                needed.append(data[base + value:data.index(b"\0", base + value)].decode())
    # Undefined dynamic symbols of the C API (Py*, _Py*), from the section headers' .dynsym.
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shentsize) for index in range(shnum)]
    python = False
    for _, kind, _, _, offset, size, link, _, _, entsize in sections:
        if kind != 11 or not entsize:
            continue
        names = sections[link][4]
        for symbol in range(offset, offset + size, entsize):
            name, _, _, shndx, _, _ = struct.unpack_from("<IBBHQQ", data, symbol)
            if name and shndx == 0 and data[names + name:names + name + 3].startswith((b"Py", b"_Py")):
                python = True
                break
    return machine, min(load[3] for load in loads), needed, python


def check_binaries(abi, files, libraries):
    """Every ELF file is for the ABI, aligned for 16 KB pages, and links what it uses from libraries there."""
    available = SYSTEM_LIBS | set(libraries) | {path.name for path in files}
    problems = []
    for path in files:
        info = elf(path)
        if info is None:
            continue
        machine, align, needed, python = info
        if machine != ABIS[abi]["machine"]:
            problems.append(f"{path}: wrong machine {machine}")
        if align < PAGE_ALIGN:
            problems.append(f"{path}: segments aligned to {align} bytes, not {PAGE_ALIGN}")
        problems += [f"{path}: links missing {name}" for name in needed if name not in available]
        # Android does not let a dlopened module see the executable's libraries: it must link libpython itself.
        if python and not {f"libpython{PY}.so", "libpython3.so"} & set(needed) and path.name != f"libpython{PY}.so":
            problems.append(f"{path}: uses Python's C API without linking libpython")
    if problems:
        raise BuildError("Binary checks failed:\n" + "\n".join(problems))


def check_private_paths(files):
    """No file may carry the build machine's home directory (user name, build paths)."""
    home = str(Path.home()).encode()
    leaks = [str(path) for path in files if home in path.read_bytes()]
    if leaks:
        raise BuildError("Build machine paths in:\n" + "\n".join(leaks[:20]))


def tree(root):
    return {path.relative_to(root).as_posix(): path for path in sorted(root.rglob("*")) if path.is_file()}


def write_zip(target, files):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, path in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return {"sha256": sha256_file(target), "size": target.stat().st_size, "files": len(files),
            "bytes": sum(path.stat().st_size for _, path in files)}


def pack(stages, worker_sha256, output):
    """Archives: files identical for every built ABI in runtime-common.zip, the rest in runtime-<abi>.zip."""
    trees = {abi: tree(root) for abi, root in stages.items()}
    common = set()
    if len(trees) > 1:
        names = set.intersection(*(set(files) for files in trees.values()))
        digests = {abi: {} for abi in trees}
        for abi, files in trees.items():
            for name in names:
                digests[abi][name] = hashlib.sha256(files[name].read_bytes()).digest()
        common = {name for name in names if len({digests[abi][name] for abi in trees}) == 1}
    assets = output / "assets" / "hermes"
    assets.mkdir(parents=True)
    jobs = {}
    first = next(iter(trees.values()))
    if common:
        jobs["runtime-common.zip"] = [(name, first[name]) for name in sorted(common)]
    for abi, files in trees.items():
        jobs[f"runtime-{abi}.zip"] = [(name, path) for name, path in files.items() if name not in common]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        futures = {name: pool.submit(write_zip, assets / name, files) for name, files in jobs.items()}
        archives = {name: future.result() for name, future in sorted(futures.items())}
    index = {
        "format": 1,
        "hermes": {"version": DESKTOP["version"], "commit": DESKTOP["commit"]},
        "python": PYTHON,
        "api_level": API,
        "executable": LAUNCHER,
        "worker_sha256": worker_sha256,
        "abis": {abi: (["runtime-common.zip"] if common else []) + [f"runtime-{abi}.zip"] for abi in trees},
        "archives": archives,
    }
    index["id"] = hashlib.sha256(json.dumps(index, sort_keys=True).encode()).hexdigest()
    (assets / "runtime.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index


def build(args):
    started = time.monotonic()
    abis = args.abi or list(ABIS)
    tools = Toolchain()
    if args.setup_toolchain:
        tools.setup(abis)
    problems = tools.missing(abis)
    if problems:
        raise BuildError("Missing toolchain: " + ", ".join(problems) + ". Run with --setup-toolchain.")
    epoch = hermes_source()
    requirements = read_requirements()
    unknown = set(args.rebuild) - set(NATIVE)
    if unknown:
        raise BuildError("Not a native package: " + ", ".join(sorted(unknown)))
    log("Checking pinned downloads...")
    build_wheelhouse(sys.executable)
    prefixes = {abi: python_package(abi) for abi in abis}
    natives = native_wheels(abis, requirements, tools, prefixes, epoch, set(args.rebuild), args.frozen)

    worker_sha256 = sha256_file(SPEC / "worker.py")
    stages = {}
    for abi in abis:
        log(f"Staging the {abi} runtime...")
        stages[abi] = stage(abi, requirements, natives[abi], prefixes[abi], worker_sha256)
    if not args.no_bytecode:
        log("Compiling bytecode...")
        compile_bytecode(stages)

    output = fresh(OUT.with_name(OUT.name + ".building"))
    log("Packing...")
    for abi in abis:
        libraries = fresh(output / "jniLibs" / abi)
        launcher(abi, tools, prefixes[abi], libraries / LAUNCHER)
        staged = list(tree(stages[abi]).values())
        needed = {name for path in staged if (info := elf(path)) for name in info[2]}
        needed |= set(elf(libraries / LAUNCHER)[2])
        for name in PYTHON_LIBS:
            # libpython3.so (the stable ABI's) only when an abi3 extension module links it.
            if name != "libpython3.so" or name in needed:
                shutil.copy2(prefixes[abi] / "lib" / name, libraries / name)
        shipped = sorted(libraries.iterdir())
        check_binaries(abi, staged + shipped, [path.name for path in shipped])
        check_private_paths(staged + shipped)
    index = pack(stages, worker_sha256, output)
    shutil.rmtree(OUT, ignore_errors=True)
    output.rename(OUT)

    log(f"Hermes {DESKTOP['version']} runtime for {', '.join(abis)} at {OUT} ({time.monotonic() - started:.0f} s)")
    for name, archive in index["archives"].items():
        log(f"    {name}: {archive['files']} files, {archive['bytes'] / 1e6:.1f} MB -> {archive['size'] / 1e6:.1f} MB")
    for abi in abis:
        size = sum(path.stat().st_size for path in (OUT / "jniLibs" / abi).iterdir())
        log(f"    jniLibs/{abi}: {size / 1e6:.1f} MB")


def cibuildwheel(argv):
    """cibuildwheel, building against pins.json's CPython for Android instead of its own newer 3.14 release."""
    from cibuildwheel.__main__ import main
    from cibuildwheel.platforms import android

    configs = [android.PythonConfiguration(version=PY, identifier=f"{CP}-android_{ABIS[abi]['arch']}",
                                           url=package["url"], sha256=package["sha256"])
               for abi, package in PINS["python"]["packages"].items()]
    android.all_python_configurations = lambda: configs
    sys.argv = ["cibuildwheel", *argv]
    main()


def main():
    if sys.argv[1:2] == ["--cibuildwheel"]:
        cibuildwheel(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Builds the Hermes runtime of the Android app.")
    parser.add_argument("--abi", action="append", choices=list(ABIS), help="build only this ABI (repeatable)")
    parser.add_argument("--setup-toolchain", action="store_true",
                        help="install the pinned NDK, CMake (sdkmanager) and Rust (rustup) first")
    parser.add_argument("--rebuild", action="append", default=[], metavar="NAME",
                        help="rebuild this native wheel even if the lock matches (repeatable)")
    parser.add_argument("--frozen", action="store_true", help="fail instead of building a wheel the lock lacks")
    parser.add_argument("--no-bytecode", action="store_true", help="ship sources only (for measurements)")
    args = parser.parse_args()
    try:
        if not os.environ.get("PRIVATE_AGENT_ANDROID_HERMES_VENV"):
            # Everything else runs in the pinned toolchain environment (CPython 3.14.0, cibuildwheel, packaging).
            python = toolchain_venv()
            os.environ["PRIVATE_AGENT_ANDROID_HERMES_VENV"] = "1"
            os.execv(python, [str(python), "-I", str(Path(__file__).resolve()), *sys.argv[1:]])
        build(args)
    except (BuildError, subprocess.CalledProcessError, OSError) as error:
        sys.exit(f"build_runtime.py: {error}")


if __name__ == "__main__":
    main()
