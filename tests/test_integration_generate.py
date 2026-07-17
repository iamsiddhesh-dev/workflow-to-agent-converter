"""Phase 4.5: end-to-end generation. Offline integration runs the pipeline on
golden specs with a fake LLM; the acceptance tests (network + key) run the real
CLI on one ops and one dev benchmark. Both walk every generated .py file's AST
and assert each import resolves to registry + stdlib + pinned deps + the
project's own modules — nothing else, ever.
"""

import ast
import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.golden_specs import REPORT_SPEC, ROUTER_SPEC
from w2a.cli import app
from w2a.generate.gapfill import GapFills
from w2a.pipeline.graph import run_pipeline

ALLOWED_THIRD_PARTY = {"crewai", "requests", "dotenv"}
PROJECT_LOCAL = {"config", "crew", "tools", "main"}


def assert_imports_allowed(project_dir: Path) -> None:
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | PROJECT_LOCAL
    for py_file in project_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                tops = {(node.module or "").split(".")[0]}
            else:
                continue
            outside = tops - allowed
            assert not outside, f"{py_file.name} imports outside the allowlist: {sorted(outside)}"


class FakeLLM:
    def call(self, prompt, response_model=None, **kw):
        assert response_model is GapFills
        return GapFills()


@pytest.mark.parametrize("spec", [ROUTER_SPEC, REPORT_SPEC], ids=["router", "report"])
def test_offline_pipeline_project_passes_import_allowlist(tmp_path, spec):
    state = run_pipeline(spec=spec, llm=FakeLLM(), out_root=str(tmp_path))
    assert state["errors"] == []
    project = state["write_result"].project_dir
    assert_imports_allowed(project)
    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator"] == "w2a"
    assert manifest["spec"]["workflow"]["name"] == spec.workflow.name
    assert {f.name for f in project.iterdir()} >= {
        "crew.py", "tools.py", "main.py", "config.py",
        "requirements.txt", ".env.example", "README.md", "manifest.json",
    }


# --- Acceptance: real end-to-end generation, one per category (network + key) ---

_HAS_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")


@pytest.mark.skipif(not _HAS_KEY, reason="no LLM API key set")
@pytest.mark.parametrize("name", ["ticket_triage", "pr_summary"], ids=["ops", "dev"])
def test_benchmark_generates_end_to_end(tmp_path, name):
    result = CliRunner().invoke(
        app,
        ["convert", f"examples/workflows/{name}.md", "--out", str(tmp_path), "--interactive"],
        input="use sensible defaults\n" * 12,
    )
    assert result.exit_code == 0, f"convert failed:\n{result.output}"

    projects = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(projects) == 1
    project = projects[0]
    assert_imports_allowed(project)

    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator"] == "w2a"
    assert manifest["pattern"]["selected"] in {"sequential", "router", "report", "approval", "watcher"}
    assert "translate" in manifest["llm_calls"]
    assert manifest["source_description"]

    tools_src = (project / "tools.py").read_text(encoding="utf-8")
    for tool in manifest["tools"]:
        if tool["resolution"] == "stub":
            assert f"# TODO: connect real {tool['name']}" in tools_src
        else:
            assert f"def {tool['builtin']}(" in tools_src
