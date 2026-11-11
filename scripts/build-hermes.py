"""Build-time only: stage a pinned Hermes source, interpreter and hashed wheels.

Windows users receive the embeddable CPython binary, not a venv redirector or a
first-run installer. Linux builds (development and CI) use the pinned relocatable
python-build-standalone 3.14.0, or a standalone 3.14.0 passed with --python for an
offline build. Every download is checked against its SHA-256 in
runtime/hermes/manifest.json. The desktop never invokes this builder.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
import platform as host_platform
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "runtime" / "hermes"
PIN = json.loads((SPEC / "manifest.json").read_text())


def call(args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def download(url, digest):
    """The pinned archive in build/, fetched once and checked against its SHA-256 every time."""
    archive = ROOT / "build" / urllib.parse.unquote(url.rsplit("/", 1)[-1])
    archive.parent.mkdir(exist_ok=True)
    if not archive.exists():
        partial = archive.with_name(archive.name + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.rename(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"Checksum mismatch: {archive.name}")
    return archive


def unpack_standalone(archive, target):
    """python-build-standalone's install_only tree (python/...) without its bundled pip or caches."""
    with tarfile.open(archive) as bundle:
        members = [
            member
            for member in bundle.getmembers()
            if "__pycache__" not in member.name.split("/") and "/site-packages/" not in member.name + "/"
        ]
        bundle.extractall(target, members=members, filter="data")


def build(output, platform, python):
    if host_platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("The bundled Hermes runtime currently supports x64 builds only")
    # pip <24.2 validates --python-version against the build interpreter instead
    # of the target runtime (pypa/pip#12216). Upgrade only the project's build venv.
    pip_version = tuple(map(int, re.match(r"(\d+)\.(\d+)", version("pip")).groups()))
    if pip_version < (24, 2):
        call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "pip==25.3"])
    source = ROOT / "build" / "hermes-source"
    if not source.exists():
        call(["git", "clone", "--depth", "1", "--branch", "v" + PIN["version"], PIN["repository"], source])
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN["commit"]:
        raise RuntimeError("Hermes source commit differs from runtime/hermes/manifest.json")
    # A modified cached checkout must not silently become a release dependency.
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"]):
        raise RuntimeError("Hermes source checkout is modified")
    stage = output.with_name(output.name + ".building")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    if platform == "win32":
        archive = download(PIN["windows_url"], PIN["windows_sha256"])
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(stage / "python")
        executable = "python/python.exe"
    elif python:
        info = json.loads(subprocess.check_output([python, "-I", "-c",
            "import sys,json; print(json.dumps([sys.base_prefix,list(sys.version_info[:3])]))"], text=True))
        if info[1] != [3, 14, 0]:
            raise RuntimeError("Hermes requires the pinned CPython 3.14.0 runtime")
        shutil.copytree(info[0], stage / "python", symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "site-packages"))
        executable = "python/bin/python3.14"
    else:
        unpack_standalone(download(PIN["linux_url"], PIN["linux_sha256"]), stage)
        executable = "python/bin/python3.14"
    # No CLI installers, repository hooks, default plugins, or optional external tools.
    # Keep all core Python modules and their data, including templates and license.
    ignored = {".git", ".github", "tests", "tests-js", "website", "web", "apps", "ui-tui", "native",
               "evals", "assets", "contributors", "node_modules", "__pycache__", "plugins", "skills",
               "optional-skills", "optional-mcps", "plugin-catalog", "docker", "nix"}
    shutil.copytree(source, stage / "source", ignore=lambda directory, names: [n for n in names if n in ignored])
    for name in ("worker.py", "manifest.json", "requirements.txt"):
        shutil.copy2(SPEC / name, stage / name)
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
           "--only-binary=:all:", "--require-hashes", "--no-deps", "--target", stage / "dependencies",
           "--python-version", "3.14", "--implementation", "cp", "--abi", "cp314",
           "--platform", "win_amd64" if platform == "win32" else "manylinux_2_28_x86_64",
           "-r", SPEC / "requirements.txt"]
    if platform != "win32":
        pip += ["--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_17_x86_64", "--platform", "linux_x86_64"]
    call(pip)
    manifest = {**PIN, "platform": platform, "arch": "x64", "executable": executable,
                "worker_sha256": hashlib.sha256((stage / "worker.py").read_bytes()).hexdigest()}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.rmtree(output, ignore_errors=True)
    stage.rename(output)
    print(f"Hermes {PIN['version']} bundled at {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("win32", "linux"), default=sys.platform)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "hermes")
    parser.add_argument("--python", default=os.environ.get("PRIVATE_AGENT_HERMES_BUILD_PYTHON"))
    args = parser.parse_args()
    build(args.output.resolve(), args.platform, args.python)
