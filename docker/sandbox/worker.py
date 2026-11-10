import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace/repository")
VENV = "/workspace/.venv/bin/python"
ARTIFACTS = Path("/artifacts")
TOOLS = ["pytest==8.3.5", "pytest-json-report==1.5.0", "pytest-cov==6.0.0"]


def command(arguments: list[str]) -> None:
    subprocess.run(arguments, check=True, cwd=ROOT)


def prepare(request: dict) -> None:
    original_files = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in ROOT.rglob("*")
        if p.is_file() and p.name not in {"uv.lock", "sandbox-resolved.txt"}
    }
    actual_python = platform.python_version()
    expected = request["python_version"]
    if not (actual_python == expected or actual_python.startswith(expected + ".")):
        raise RuntimeError(
            f"Image Python {actual_python} differs from requested {expected}"
        )
    overrides = request["dependency_overrides"]
    command(["uv", "venv", "--python", sys.executable, "/workspace/.venv"])
    constraints = ARTIFACTS / "overrides.txt"
    constraints.write_text(
        "".join(f"{name}=={version}\n" for name, version in overrides.items())
    )
    upgrades = [
        argument
        for name, version in sorted(overrides.items())
        for argument in ["--upgrade-package", f"{name}=={version}"]
    ]
    environment = dict(os.environ, UV_PROJECT_ENVIRONMENT="/workspace/.venv")
    if (ROOT / "uv.lock").exists() or (ROOT / "pyproject.toml").exists():
        if (ROOT / "poetry.lock").exists() and not (ROOT / "uv.lock").exists():
            raise RuntimeError(
                "poetry.lock without uv.lock is unsupported; export locked requirements first"
            )
        if overrides:
            command(["uv", "lock", *upgrades])
        elif (ROOT / "uv.lock").exists():
            command(["uv", "lock", "--locked"])
        else:
            command(["uv", "lock"])
        subprocess.run(
            ["uv", "sync", "--locked", "--no-editable", "--python", sys.executable],
            env=environment,
            check=True,
            cwd=ROOT,
        )
        shutil.copyfile(ROOT / "uv.lock", ARTIFACTS / "resolved.lock")
    elif (ROOT / "requirements.txt").exists():
        resolved = ROOT / "sandbox-resolved.txt"
        command(
            [
                "uv",
                "pip",
                "compile",
                "requirements.txt",
                "--python",
                sys.executable,
                "--output-file",
                str(resolved),
                "--constraint",
                str(constraints),
                *upgrades,
            ]
        )
        command(["uv", "pip", "sync", "--python", VENV, str(resolved)])
        shutil.copyfile(resolved, ARTIFACTS / "resolved.lock")
    else:
        raise RuntimeError("No pyproject.toml or requirements.txt in repository")
    installed = subprocess.check_output(
        ["uv", "pip", "freeze", "--python", VENV], text=True
    )
    pinned = ARTIFACTS / "installed.txt"
    pinned.write_text(
        "\n".join(
            line
            for line in installed.splitlines()
            if re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s]+", line)
        )
    )
    command(
        ["uv", "pip", "install", "--python", VENV, "--constraint", str(pinned), *TOOLS]
    )
    command(["uv", "pip", "check", "--python", VENV])
    packages = json.loads(
        subprocess.check_output(
            ["uv", "pip", "list", "--python", VENV, "--format", "json"]
        )
    )
    versions = {
        re.sub(r"[-_.]+", "-", p["name"]).lower(): p["version"] for p in packages
    }
    for package, version in overrides.items():
        if versions.get(package) != version:
            raise RuntimeError(
                f"Requested dependency not installed: {package}=={version}"
            )
    for relative, digest in original_files.items():
        path = ROOT / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise RuntimeError(
                f"Dependency preparation modified source file: {relative}"
            )
    (ARTIFACTS / "preparation.json").write_text(
        json.dumps(
            {"python_version": actual_python, "installed_dependencies": versions}
        )
    )


def run_tests(request: dict) -> int:
    os.environ.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTEST_ADDOPTS="",
        COVERAGE_FILE="/tmp/.coverage",
        PYTHONPATH="/workspace/repository:/workspace/repository/src",
    )
    arguments = [
        VENV,
        "-m",
        "pytest",
        "-p",
        "pytest_jsonreport.plugin",
        "-p",
        "pytest_cov.plugin",
        "-p",
        "no:cacheprovider",
        "--override-ini",
        "addopts=",
        "--json-report",
        "--json-report-file=/artifacts/report.json",
        "--cov=/workspace/repository",
        "--cov-branch",
        "--cov-report=json:/artifacts/coverage.json",
        "-q",
    ]
    arguments += [
        f"/generated/{path[10:]}"
        if path.startswith("generated/")
        else f"/workspace/repository/{path[11:]}"
        for path in request["test_paths"]
    ]
    return subprocess.call(arguments, cwd=ROOT)


if __name__ == "__main__":
    mode, raw = sys.argv[1:]
    request = json.loads(raw)
    if mode == "prepare":
        prepare(request)
    elif mode == "test":
        sys.exit(run_tests(request))
    else:
        raise ValueError("Unsupported sandbox operation")
