"""Phase 7.4: diversity check — two different workflows must not generate
near-identical prose (a gap-fill that always writes the same generic paragraph
regardless of the spec would pass specificity per-project while being
boilerplate across the fleet; this is the cross-project check that catches it).
"""

from __future__ import annotations

from tests.golden_specs import GOLDEN_SPECS
from w2a.templates.render import render_pattern
from w2a.validate.diversity import check_diversity, check_project_diversity, extract_prompt_text


def test_extract_prompt_text_pulls_prose_not_scaffolding():
    source = (
        "from crewai import Agent, Task\n"
        "agent_x = Agent(role='Ticket Classifier', goal='classify fast', backstory='seasoned triager', llm=_llm)\n"
        "task_x = Task(description='Read the ticket and label it.', expected_output='a label', agent=agent_x)\n"
    )
    prose = extract_prompt_text(source)
    assert "Ticket Classifier" in prose
    assert "seasoned triager" in prose
    assert "Read the ticket" in prose
    assert "crewai" not in prose  # import statement is scaffolding, not prose
    assert "expected_output" not in prose  # not a tracked prose kwarg


def test_two_different_golden_projects_are_diverse():
    router_files = render_pattern(GOLDEN_SPECS["router"], "router")
    report_files = render_pattern(GOLDEN_SPECS["report"], "report")
    result = check_project_diversity(router_files["crew.py"], report_files["crew.py"])
    assert result.ok
    assert result.overlap < 0.5


def test_near_duplicate_prose_fails_diversity():
    prose_a = (
        "This agent is a capable professional who processes the input and returns a result. "
        "The task is to process the input and produce the expected output for the workflow."
    )
    prose_b = (
        "This agent is a capable professional who processes the input and returns a result. "
        "The task is to process the input and produce the expected output for the system."
    )
    result = check_diversity(prose_a, prose_b)
    assert not result.ok
    assert result.overlap > 0.5


def test_empty_prose_is_trivially_diverse():
    result = check_diversity("", "some unrelated prose here about things")
    assert result.ok
    assert result.overlap == 0.0
