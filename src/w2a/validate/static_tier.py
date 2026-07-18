"""Static tier: prove a generated project is well-formed before spending a
subprocess (env tier) or a crew run (exec tier) on it.

Three checks, cheapest first: every file compiles, ``ruff`` finds nothing,
and every import resolves to stdlib + the pinned third-party deps + the
project's own modules. The import-allowlist walk is the same AST check
``tests/test_integration_generate.py`` used for the Phase 4 integration
tests — it lives here now so the repair loop (5.5) and any test can share
one definition of "allowed" instead of two that can drift apart.
"""

from __future__ import annotations

import ast
import json
import py_compile
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_THIRD_PARTY = {"crewai", "requests", "dotenv"}
PROJECT_LOCAL = {"config", "crew", "tools", "main"}


@dataclass
class CheckResult:
    name: str
    ok: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class StaticTierReport:
    ok: bool
    checks: list[CheckResult]

    def __str__(self) -> str:
        lines = [f"static tier: {'pass' if self.ok else 'fail'}"]
        for check in self.checks:
            lines.append(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}")
            lines.extend(f"      {issue}" for issue in check.issues)
        return "\n".join(lines)


def _py_files(project_dir: Path) -> list[Path]:
    return sorted(project_dir.glob("*.py"))


def check_compile(project_dir: Path) -> CheckResult:
    # NB: py_compile.compile silently drops doraise's exception when quiet=2
    # (stdlib quirk: `if quiet < 2: if doraise: raise py_exc` — quiet=2 skips
    # both the print AND the raise). quiet=1 keeps doraise honest.
    issues = []
    for f in _py_files(project_dir):
        try:
            py_compile.compile(str(f), doraise=True, quiet=1)
        except py_compile.PyCompileError as exc:
            issues.append(f"{f.name}: {exc.msg}")
    return CheckResult("py_compile", not issues, issues)


def check_ruff(project_dir: Path) -> CheckResult:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--output-format=json", str(project_dir)],
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return CheckResult("ruff", False, [f"could not run ruff: {exc}"])

    try:
        violations = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return CheckResult("ruff", False, [(result.stdout or result.stderr).strip()])

    issues = [
        f"{Path(v['filename']).name}:{v['location']['row']} {v['code']} {v['message']}"
        for v in violations
    ]
    return CheckResult("ruff", not issues, issues)


def import_allowlist_issues(project_dir: Path) -> list[str]:
    """Every import in the project that resolves outside stdlib + pinned deps + local modules."""
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | PROJECT_LOCAL
    issues: list[str] = []
    for py_file in _py_files(project_dir):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            issues.append(f"{py_file.name}: cannot check imports, syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import — always local
                    continue
                tops = {(node.module or "").split(".")[0]}
            else:
                continue
            outside = tops - allowed
            if outside:
                issues.append(f"{py_file.name} imports outside the allowlist: {sorted(outside)}")
    return issues


def check_import_allowlist(project_dir: Path) -> CheckResult:
    issues = import_allowlist_issues(project_dir)
    return CheckResult("import_allowlist", not issues, issues)


def run_static_tier(project_dir: Path) -> StaticTierReport:
    checks = [
        check_compile(project_dir),
        check_ruff(project_dir),
        check_import_allowlist(project_dir),
    ]
    return StaticTierReport(ok=all(c.ok for c in checks), checks=checks)
