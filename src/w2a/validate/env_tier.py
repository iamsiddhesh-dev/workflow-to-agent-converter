"""Environment tier: does the project actually install and import in a venv
that only has *its own* ``requirements.txt``, not whatever happens to be on
the developer's machine?

Static tier proves the code is well-formed against an assumed allowlist; this
tier proves the assumption — every import in ``requirements.txt`` really
resolves, and nothing the project needs is missing or mispinned. ``uv venv``
is used when available (much faster), falling back to the stdlib ``venv``
module. The venv is built from the *running interpreter* (``sys.executable``)
so it inherits whatever Python version this process was launched with — on
this project that must be a 3.11 venv, never the system default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 300.0


@dataclass
class EnvTierReport:
    ok: bool
    venv_created: bool
    install_ok: bool
    import_ok: bool
    issues: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"env tier: {'pass' if self.ok else 'fail'}"]
        lines.extend(f"  {issue}" for issue in self.issues)
        return "\n".join(lines)


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(argv: list[str], *, timeout: float, **kw) -> tuple[bool, str | None]:
    """subprocess.run wrapper that turns a timeout into a clean (False, message)
    result instead of letting ``TimeoutExpired`` propagate as a bare traceback —
    a validator tier must never crash out of the caller regardless of load."""
    try:
        result = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, **kw
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s: {' '.join(argv)}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, None


def _create_venv(venv_dir: Path, base_python: str) -> tuple[bool, str | None]:
    if shutil.which("uv"):
        ok, err = _run(["uv", "venv", "--python", base_python, str(venv_dir)], timeout=120)
        if ok:
            return True, None
        # fall through to stdlib venv on any uv failure
    return _run([base_python, "-m", "venv", str(venv_dir)], timeout=120)


def _install_requirements(venv_python: Path, requirements: Path) -> tuple[bool, str | None]:
    if shutil.which("uv"):
        argv = ["uv", "pip", "install", "--python", str(venv_python), "-r", str(requirements)]
    else:
        argv = [str(venv_python), "-m", "pip", "install", "-q", "-r", str(requirements)]
    return _run(argv, timeout=DEFAULT_TIMEOUT)


def _import_modules(venv_python: Path, project_dir: Path) -> tuple[bool, str | None]:
    modules = [
        p.stem
        for p in sorted(project_dir.glob("*.py"))
        if p.stem not in {"__init__"}
    ]
    if not modules:
        return True, None
    script = "; ".join(f"import {m}" for m in modules)
    return _run(
        [str(venv_python), "-c", script],
        timeout=60,
        cwd=str(project_dir),
        env={**os.environ, "MOCK_MODE": "1", "PYTHONIOENCODING": "utf-8"},
    )


def run_env_tier(
    project_dir: Path,
    base_python: str | None = None,
    keep_venv: bool = False,
) -> EnvTierReport:
    base_python = base_python or sys.executable
    requirements = project_dir / "requirements.txt"
    if not requirements.exists():
        return EnvTierReport(
            ok=False, venv_created=False, install_ok=False, import_ok=False,
            issues=["requirements.txt not found in project"],
        )

    tmp_dir = tempfile.mkdtemp(prefix="w2a_envtier_")
    venv_dir = Path(tmp_dir) / "venv"
    try:
        created, err = _create_venv(venv_dir, base_python)
        if not created:
            return EnvTierReport(
                ok=False, venv_created=False, install_ok=False, import_ok=False,
                issues=[f"venv creation failed: {err}"],
            )

        venv_python = _venv_python(venv_dir)
        installed, err = _install_requirements(venv_python, requirements)
        if not installed:
            return EnvTierReport(
                ok=False, venv_created=True, install_ok=False, import_ok=False,
                issues=[f"pip install failed: {err}"],
            )

        imported, err = _import_modules(venv_python, project_dir)
        if not imported:
            return EnvTierReport(
                ok=False, venv_created=True, install_ok=True, import_ok=False,
                issues=[f"import check failed: {err}"],
            )

        return EnvTierReport(ok=True, venv_created=True, install_ok=True, import_ok=True)
    finally:
        if not keep_venv:
            shutil.rmtree(tmp_dir, ignore_errors=True)
