"""NL description -> WorkflowSpec translation.

The intellectual core of the converter: turn a messy, underspecified founder
paragraph into a structured design. The single hard rule is *don't confabulate* —
unknowns are routed into ``ambiguities[]`` (questions whose answers change the
design) or ``assumptions[]`` (defaults where any reasonable choice works), never
silently invented. Structured-output parsing and the malformed-JSON re-ask loop
are the LLM wrapper's job (``llm.LLM.call`` with ``response_model``); this module
owns only the prompt.
"""

from __future__ import annotations

import json

from w2a.llm import LLM
from w2a.spec.model import (
    AgentSpec,
    Flow,
    TaskSpec,
    ToolSpec,
    Workflow,
    WorkflowSpec,
)

# Two worked examples — one ops, one dev/eng — built as validated spec objects so
# they can never drift from the schema. Rendered into the prompt below.

_ONBOARDING_INPUT = (
    "When someone new joins, as soon as HR gives us a start date I need their laptop "
    "ordered and accounts set up in Slack, email, and whatever tools their team uses "
    "(eng and sales use different stuff). A few days before they start, send them a "
    "welcome email with first-day info. On day one, create a checklist doc for their "
    "manager with the intro meetings for that first week. About a week in, check that "
    "everything actually got set up and flag anything still missing. Nothing needs approval."
)

_ONBOARDING_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="Employee Onboarding",
        description="Coordinate equipment, account setup, first-day communications, and a follow-up check for each new hire.",
        trigger="HR provides a new hire's start date.",
        category="ops",
    ),
    agents=[
        AgentSpec(
            id="provisioner",
            role="Onboarding Provisioner",
            goal="Order equipment and create the new hire's accounts across the right team's tools.",
            backstory_hint="Detail-oriented IT ops coordinator who never lets a setup step slip.",
        ),
        AgentSpec(
            id="communicator",
            role="Onboarding Communicator",
            goal="Prepare first-day communications and the manager's intro-meeting checklist.",
            backstory_hint="Warm people-ops coordinator focused on a smooth first week.",
        ),
        AgentSpec(
            id="verifier",
            role="Setup Verifier",
            goal="Confirm accounts and equipment are working a week in and flag anything missing.",
            backstory_hint="Skeptical checker who assumes something is broken until proven otherwise.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="provision_accounts",
            description="Order the laptop and create Slack, email, and team-specific tool accounts for the new hire.",
            agent_id="provisioner",
            expected_output="A record of the ordered equipment and the accounts created per system.",
        ),
        TaskSpec(
            id="send_welcome_email",
            description="Send the new hire a welcome email with first-day logistics a few days before they start.",
            agent_id="communicator",
            depends_on=["provision_accounts"],
            expected_output="A welcome email with first-day logistics delivered to the new hire.",
        ),
        TaskSpec(
            id="create_intro_checklist",
            description="Create a first-week intro-meeting checklist document for the new hire's manager.",
            agent_id="communicator",
            depends_on=["send_welcome_email"],
            expected_output="A checklist document listing the manager's intro meetings for week one.",
        ),
        TaskSpec(
            id="verify_setup",
            description="A week in, verify accounts and equipment are working and flag anything still missing.",
            agent_id="verifier",
            depends_on=["create_intro_checklist"],
            expected_output="A list of any accounts or equipment still missing or not working.",
        ),
    ],
    tools=[
        ToolSpec(
            id="equipment_ordering",
            name="equipment ordering",
            purpose="Order the laptop and equipment for the new hire.",
            category="external",
            inputs="new hire details, equipment type",
            outputs="order confirmation",
        ),
        ToolSpec(
            id="account_provisioning",
            name="account provisioning",
            purpose="Create Slack, email, and team-specific tool accounts.",
            category="external",
            inputs="new hire details, team",
            outputs="list of created accounts",
        ),
        ToolSpec(
            id="send_message",
            name="send message",
            purpose="Send the welcome email to the new hire.",
            category="builtin",
            inputs="recipient, subject, body",
            outputs="send confirmation",
        ),
        ToolSpec(
            id="write_file",
            name="write file",
            purpose="Write the manager's intro-meeting checklist document.",
            category="builtin",
            inputs="path, content",
            outputs="written file path",
        ),
    ],
    flow=Flow(
        pattern="sequential",
        edges=[
            ("provision_accounts", "send_welcome_email"),
            ("send_welcome_email", "create_intro_checklist"),
            ("create_intro_checklist", "verify_setup"),
        ],
    ),
    assumptions=[
        "The provisioner selects the account set (eng vs sales tools) based on the new hire's team.",
        "The welcome email is sent a few days before the start date.",
    ],
    ambiguities=[],
)

_PR_SUMMARY_INPUT = (
    "Our PRs are big enough that reviewers just skim and approve. I want a summary for "
    "every PR that explains what actually changed and why it matters — the real behavior "
    "change, whether it touches anything risky (migrations, auth, prod config), and whether "
    "tests cover it. Post it as a comment on the PR. If the diff is trivial (typo, comment) "
    "a one-liner is fine."
)

_PR_SUMMARY_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="PR Summary Generator",
        description="Generate a reviewer-facing summary of each pull request and post it as a comment.",
        trigger="A pull request is opened or updated.",
        category="dev",
    ),
    agents=[
        AgentSpec(
            id="analyzer",
            role="Diff Analyzer",
            goal="Determine the real behavior change, risk areas, and test coverage of a PR diff.",
            backstory_hint="Senior engineer who reviews for actual risk, not surface churn.",
        ),
        AgentSpec(
            id="summarizer",
            role="Summary Writer",
            goal="Write a concise reviewer-facing summary and post it on the PR.",
            backstory_hint="Clear technical writer who front-loads what a reviewer must know.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="analyze_diff",
            description="Read the PR diff and assess the behavior change, risky areas, and whether tests cover it.",
            agent_id="analyzer",
            expected_output="An assessment of the behavior change, risky areas (migrations, auth, prod config), and test coverage.",
        ),
        TaskSpec(
            id="post_summary",
            description="Write the reviewer summary and post it as a PR comment; a one-liner if the diff is trivial.",
            agent_id="summarizer",
            depends_on=["analyze_diff"],
            expected_output="A summary comment posted on the PR (full for substantive diffs, one line for trivial ones).",
        ),
    ],
    tools=[
        ToolSpec(
            id="fetch_pr_diff",
            name="fetch PR diff",
            purpose="Retrieve the diff and metadata for the pull request.",
            category="external",
            inputs="pull request identifier",
            outputs="diff text and changed-file list",
        ),
        ToolSpec(
            id="post_pr_comment",
            name="post PR comment",
            purpose="Post the generated summary as a comment on the pull request.",
            category="external",
            inputs="pull request identifier, comment body",
            outputs="posted-comment confirmation",
        ),
    ],
    flow=Flow(
        pattern="report",
        edges=[("analyze_diff", "post_summary")],
    ),
    assumptions=[
        "Trivial diffs (typo or comment-only changes) get a one-line summary instead of the full treatment.",
    ],
    ambiguities=[],
)

_RULES = """\
Convert this plain-language business process into a WorkflowSpec JSON matching the
schema below. Rules:
- Do NOT invent specifics the text does not support. Route every unknown to one of:
  * ambiguities[] — a QUESTION for the user, when the answer would change the design
    (e.g. how many roles, whether a human must approve, what triggers it).
  * assumptions[] — the default you chose, when any reasonable default works
    (e.g. exact naming, minor ordering). State it as the choice you made.
- Every task needs exactly one owning agent (agent_id) that exists in agents[].
- Every tool needs a purpose grounded in the text; do not add tools no task needs.
- Prefer fewer, well-defined agents (2-4) over one agent per sentence.
- Give every task a concrete, non-empty expected_output.
- flow.pattern is the dominant shape: sequential | router | report | approval | watcher.
  Put human_checkpoint=true on any step the text implies someone must approve.
- flow.edges are (from_task_id, to_task_id) pairs over existing task ids.
- When the description is too vague to design a real workflow, keep agents/tasks minimal
  and put the real design questions in ambiguities[] rather than fabricating detail.
Output ONLY the JSON object, no prose, no markdown fences."""


def _schema_text() -> str:
    return json.dumps(WorkflowSpec.model_json_schema(), indent=2)


def _example_block(label: str, input_text: str, spec: WorkflowSpec) -> str:
    return (
        f"### Example ({label})\n"
        f"INPUT:\n{input_text}\n\n"
        f"OUTPUT:\n{spec.model_dump_json(indent=2)}"
    )


def build_prompt(description: str, extra_context: str | None = None) -> str:
    """Assemble the full translation prompt for one description."""
    parts = [
        _RULES,
        "\nSCHEMA:\n" + _schema_text(),
        "\n" + _example_block("ops", _ONBOARDING_INPUT, _ONBOARDING_SPEC),
        "\n" + _example_block("dev", _PR_SUMMARY_INPUT, _PR_SUMMARY_SPEC),
    ]
    if extra_context:
        parts.append("\nADDITIONAL ANSWERS FROM THE USER (fold these in, remove any ambiguities they resolve):\n" + extra_context)
    parts.append("\n### Now convert this description\nINPUT:\n" + description + "\n\nOUTPUT:")
    return "\n".join(parts)


def translate(description: str, llm: LLM | None = None, extra_context: str | None = None) -> WorkflowSpec:
    """Translate a plain-language description into a validated WorkflowSpec.

    The malformed-output re-ask loop and raw-output-on-failure dump live in
    ``LLM.call`` (it raises ``LLMResponseError`` after exhausting retries); this
    function does not reimplement retry logic. ``extra_context`` carries user
    answers back in for clarify-mode re-translation.
    """
    llm = llm or LLM()
    prompt = build_prompt(description, extra_context=extra_context)
    return llm.call(prompt, response_model=WorkflowSpec)
