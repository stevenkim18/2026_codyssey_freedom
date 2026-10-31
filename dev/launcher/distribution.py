"""Build the reviewed PC-only application using an already installed PyInstaller.

Run from dev with the build Python: python -m launcher.distribution --output PATH.
No dependency installation, signing identity discovery, or model downloads occur.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tarfile


LAUNCHER_FILES = tuple("launcher/" + name for name in (
    "__init__.py", "__main__.py", "profiles.py", "doctor.py", "standalone.py",
    "freeze_entry.py", "text_only_instructions.md",
))
HIDDEN_IMPORTS = (
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
)
EXCLUDES = (
    "torch", "transformers", "gliner", "numpy", "scipy", "pandas", "pytest",
    "hypothesis", "IPython", "matplotlib", "tensorflow", "keras", "sklearn",
    "jupyter", "notebook", "PIL", "cv2", "sympy", "setuptools", "pip",
    "conda", "uvloop", "httptools", "websockets", "wsproto", "watchfiles",
    "pydantic_settings", "email_validator", "rich",
)
PACKAGES = ("pyinstaller", "pyinstaller-hooks-contrib", "fastapi", "starlette",
            "uvicorn", "pydantic", "pydantic-core", "httpx", "httpcore", "anyio", "h11")
VERSION = "0.1.0-engineering.1"
ARTIFACT = f"pii-gateway-macos-arm64-{VERSION}.tar.gz"


class BuildError(Exception):
    """Safe diagnostic code with no environment or credentials attached."""


def _sources(root):
    names = [str(path.relative_to(root)) for path in (root / "gateway").rglob("*.py")
             if not any(part in {"tests", "__pycache__"} or part.startswith(".")
                        for part in path.relative_to(root).parts)]
    names += [*LAUNCHER_FILES, "detector/__init__.py", "detector/schemas.py"]
    sources = {}
    for name in sorted(names):
        path = root / name
        if (not path.is_file() or path.is_symlink()
                or any(parent.is_symlink() for parent in path.parents if parent != root.parent)):
            raise BuildError("unsafe_source")
        sources[name] = path.read_bytes()
    if "gateway/__init__.py" not in sources:
        raise BuildError("missing_gateway_source")
    return sources


def _write_json(path, data):
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _archive_bundle(output):
    """Preserve native framework symlinks, refusing paths escaping the bundle."""
    bundle = output / "dist/pii-gateway"
    if bundle.is_symlink() or not bundle.is_dir():
        raise BuildError("unsafe_bundle")
    resolved_bundle = bundle.resolve(strict=True)
    paths = [bundle, *bundle.rglob("*")]
    for path in paths:
        relative = path.relative_to(bundle.parent)
        if relative.is_absolute() or ".." in relative.parts:
            raise BuildError("unsafe_bundle")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError):
                raise BuildError("unsafe_bundle_link") from None
            # Absolute links would retain host paths after extraction.
            if Path(path.readlink()).is_absolute() or not target.is_relative_to(resolved_bundle):
                raise BuildError("unsafe_bundle_link")
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BuildError("unsafe_bundle_type")
    artifact = output / ARTIFACT
    with tarfile.open(artifact, "w:gz", dereference=False) as archive:
        # Validation precedes archiving; add explicitly so symlink dirs are not followed.
        for path in sorted(paths):
            archive.add(path, arcname=path.relative_to(bundle.parent).as_posix(), recursive=False)
    digest = hashlib.sha256()
    with artifact.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    (output / (ARTIFACT + ".sha256")).write_text(value + "  " + ARTIFACT + "\n", encoding="ascii")
    manifest = {"schema_version": "1.0", "version": VERSION, "target": "macos-arm64",
                "artifact": ARTIFACT, "sha256": value, "notarized": False,
                "signature_verification": "not_performed", "runtime_verification": "not_performed"}
    _write_json(output / "artifact-manifest.json", manifest)
    return manifest


def build(output, *, source_root=None, plan=False):
    output = Path(output)
    if not output.is_absolute():
        raise BuildError("absolute_output_required")
    if output.exists() or output.is_symlink():
        raise BuildError("output_exists")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise BuildError("requires_macos_arm64")
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            raise BuildError("missing_build_dependency_" + name) from None
    root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    sources = _sources(root)
    evidence = {"schema_version": "1.0", "files": {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for name, content in sources.items()}}
    receipt = {"schema_version": "1.0", "target": "macos-arm64", "format": "onedir",
               "python": platform.python_version(), "dependencies": versions,
               "notarized": False, "signature_verification": "not_performed",
               "status": "planned", "source_bytes": sum(map(len, sources.values())),
               "source_file_count": len(sources), "runtime_verification": "not_performed"}
    if plan:
        return receipt
    # Atomic mkdir deliberately refuses existing directories; never reuse/erase builds.
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise BuildError("output_exists") from None
    stage = output / "source"
    for name, content in sources.items():
        destination = stage / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o600)
    _write_json(output / "source-evidence.json", evidence)
    receipt["status"] = "building"
    _write_json(output / "build-receipt.json", receipt)
    for directory in ("home", "tmp", "pyinstaller-config"):
        (output / directory).mkdir(mode=0o700)
    # A fresh HOME also prevents hooks discovering private local configuration.
    environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                   "HOME": str(output / "home"), "TMPDIR": str(output / "tmp"),
                   "LANG": "en_US.UTF-8",
                   "PYINSTALLER_CONFIG_DIR": str(output / "pyinstaller-config")}
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--onedir", "--name", "pii-gateway", "--target-arch", "arm64",
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"),
               "--specpath", str(output / "spec"), "--paths", str(stage),
               "--add-data", str(stage / "launcher/text_only_instructions.md") + ":launcher"]
    for name in HIDDEN_IMPORTS:
        command += ["--hidden-import", name]
    for name in EXCLUDES:
        command += ["--exclude-module", name]
    command.append(str(stage / "launcher/freeze_entry.py"))
    try:
        with (output / "build.log").open("w", encoding="utf-8") as log:
            subprocess.run(command, cwd=stage, env=environment, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        executable = output / "dist/pii-gateway/pii-gateway"
        if not executable.is_file():
            raise BuildError("build_executable_missing")
        receipt["executable_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
        artifact = _archive_bundle(output)
        receipt.update({"artifact": artifact["artifact"], "artifact_sha256": artifact["sha256"],
                        "version": VERSION, "status": "built"})
    except (OSError, subprocess.CalledProcessError, BuildError):
        receipt["status"] = "failed"
        _write_json(output / "build-receipt.json", receipt)
        raise BuildError("pyinstaller_failed") from None
    _write_json(output / "build-receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.output, plan=args.plan), sort_keys=True))
    except (BuildError, OSError) as error:
        code = str(error) if isinstance(error, BuildError) else "build_filesystem_error"
        print(json.dumps({"status": "error", "code": code}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
