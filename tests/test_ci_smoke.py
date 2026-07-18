"""CI smoke test (Phase 8.4): convert -> static+exec validate, no key, no fresh venv.

Runs offline (a fake gap-fill LLM, same pattern as test_integration_generate.py)
so it's fast and keyless. Deliberately skips the env tier (real venv + install)
and the LLM-touching repair loop -- those need minutes and an API key and are
covered by the full local suite, not CI.
"""

from tests.golden_specs import ROUTER_SPEC
from w2a.generate.gapfill import GapFills
from w2a.pipeline.graph import run_pipeline
from w2a.validate.exec_tier import run_exec_tier
from w2a.validate.static_tier import run_static_tier


class FakeLLM:
    def call(self, prompt, response_model=None, **kw):
        assert response_model is GapFills
        return GapFills()


def test_mock_mode_convert_then_validate_smoke(tmp_path):
    state = run_pipeline(spec=ROUTER_SPEC, llm=FakeLLM(), out_root=str(tmp_path))
    assert state["errors"] == []
    project_dir = state["write_result"].project_dir

    static_report = run_static_tier(project_dir)
    assert static_report.ok, static_report

    exec_report = run_exec_tier(project_dir)
    assert exec_report.ok, exec_report
    assert not exec_report.tasks_missing, exec_report
