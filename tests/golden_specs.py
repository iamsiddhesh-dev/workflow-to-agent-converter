"""Hand-written WorkflowSpec fixtures, one per Pattern Vault pattern (Phase 3.5).

These are not translated by the LLM — they're built directly as validated
Pydantic objects so they can never drift from the schema, the same approach
``spec/translate.py`` uses for its worked examples. Shared by the golden
render test and the selector test: both need one spec per pattern whose task
graph actually has that pattern's shape.
"""

from __future__ import annotations

from w2a.spec.model import AgentSpec, Flow, TaskSpec, ToolSpec, Workflow, WorkflowSpec
from w2a.spec.translate import _ONBOARDING_SPEC

SEQUENTIAL_SPEC = _ONBOARDING_SPEC

ROUTER_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="Support Ticket Triage",
        description="Classify incoming support tickets and route urgent ones to on-call immediately.",
        trigger="A support ticket arrives.",
        category="ops",
    ),
    agents=[
        AgentSpec(
            id="classifier",
            role="Ticket Classifier",
            goal="Read each ticket and decide whether it's a bug, billing issue, or question.",
            backstory_hint="Fast, consistent triager who never lets a ticket sit unlabeled.",
        ),
        AgentSpec(
            id="bug_handler",
            role="Bug Router",
            goal="File bug-labeled tickets for engineering to pick up.",
            backstory_hint="Engineer-minded, translates customer language into bug reports.",
        ),
        AgentSpec(
            id="billing_handler",
            role="Billing Router",
            goal="File billing-labeled tickets for the billing team to pick up.",
            backstory_hint="Detail-oriented with account and invoice issues.",
        ),
        AgentSpec(
            id="escalator",
            role="Urgent Escalator",
            goal="Ping whoever is on-call the moment a ticket sounds urgent.",
            backstory_hint="Treats every 'down' or 'can't login' as real until proven otherwise.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="classify_ticket",
            description="Read the ticket and decide if it's a bug, billing issue, or question; flag it urgent if the customer reports being down or losing money.",
            agent_id="classifier",
            expected_output="A category label (bug/billing/question) and an urgency flag.",
        ),
        TaskSpec(
            id="route_bug",
            description="File the ticket as a bug for engineering, labeled and queued for business hours.",
            agent_id="bug_handler",
            depends_on=["classify_ticket"],
            expected_output="The ticket labeled 'bug' and queued for engineering.",
        ),
        TaskSpec(
            id="route_billing",
            description="File the ticket as a billing issue, labeled and queued for business hours.",
            agent_id="billing_handler",
            depends_on=["classify_ticket"],
            expected_output="The ticket labeled 'billing' and queued for the billing team.",
        ),
        TaskSpec(
            id="notify_oncall",
            description="If the ticket is urgent, immediately ping whoever is on-call.",
            agent_id="escalator",
            depends_on=["classify_ticket"],
            expected_output="An on-call notification sent, or nothing if the ticket wasn't urgent.",
        ),
    ],
    tools=[
        ToolSpec(
            id="send_message",
            name="send message",
            purpose="Ping the on-call channel for urgent tickets.",
            category="builtin",
            inputs="channel, message",
            outputs="send confirmation",
        ),
    ],
    flow=Flow(
        pattern="router",
        edges=[
            ("classify_ticket", "route_bug"),
            ("classify_ticket", "route_billing"),
            ("classify_ticket", "notify_oncall"),
        ],
    ),
    assumptions=["Questions that are neither bugs nor billing are treated like billing for routing purposes until a dedicated queue exists."],
    ambiguities=[],
)

REPORT_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="Weekly Founder Report",
        description="Pull engineering, support, and finance updates together into one Friday status report.",
        trigger="Every Friday morning.",
        category="ops",
    ),
    agents=[
        AgentSpec(
            id="eng_gatherer",
            role="Engineering Summarizer",
            goal="Summarize what shipped and what's blocked in engineering this week.",
            backstory_hint="Reads ticket trackers for a living, terse and accurate.",
        ),
        AgentSpec(
            id="support_gatherer",
            role="Support Summarizer",
            goal="Summarize ticket counts and categories from the support queue this week.",
            backstory_hint="Sees every ticket pattern before anyone else does.",
        ),
        AgentSpec(
            id="finance_gatherer",
            role="Finance Summarizer",
            goal="Summarize burn rate and spend notes dropped by finance this week.",
            backstory_hint="Numbers-first, translates spreadsheets into plain language.",
        ),
        AgentSpec(
            id="compiler",
            role="Report Compiler",
            goal="Combine all three summaries into one readable status report.",
            backstory_hint="Plain-language writer who hates copy-paste busywork.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="gather_eng",
            description="Summarize engineering tickets closed out this week.",
            agent_id="eng_gatherer",
            expected_output="A short summary of what shipped and what's blocked.",
        ),
        TaskSpec(
            id="gather_support",
            description="Pull ticket counts and categories from the support queue this week.",
            agent_id="support_gatherer",
            expected_output="Ticket counts by category, with anything that spiked called out.",
        ),
        TaskSpec(
            id="gather_finance",
            description="Summarize whatever finance dropped in the shared folder this week.",
            agent_id="finance_gatherer",
            expected_output="A short burn-rate and spend summary.",
        ),
        TaskSpec(
            id="compile_report",
            description="Combine the engineering, support, and finance summaries into one status report with a few plain-language sections.",
            agent_id="compiler",
            depends_on=["gather_eng", "gather_support", "gather_finance"],
            expected_output="A single status report document, ready for the requester to review before sending.",
        ),
    ],
    tools=[
        ToolSpec(
            id="write_markdown_report",
            name="write markdown report",
            purpose="Write the final status report as a markdown document.",
            category="builtin",
            inputs="title, sections",
            outputs="path to written report",
        ),
    ],
    flow=Flow(
        pattern="report",
        edges=[
            ("gather_eng", "compile_report"),
            ("gather_support", "compile_report"),
            ("gather_finance", "compile_report"),
        ],
    ),
    assumptions=["The report is prepared for review, not auto-sent to the founders."],
    ambiguities=[],
)

APPROVAL_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="Bug Severity Triage",
        description="Triage incoming bug reports, file a severity label and suggested owner, and require human confirmation before anything pages on-call.",
        trigger="A bug report comes in from support, Sentry, or a team member.",
        category="dev",
    ),
    agents=[
        AgentSpec(
            id="triager",
            role="Bug Triager",
            goal="Assess severity and likely system area for an incoming bug report.",
            backstory_hint="Has seen enough false alarms to weigh evidence, not vibes.",
        ),
        AgentSpec(
            id="confirmer",
            role="P0 Confirmer",
            goal="Confirm or reject a suspected P0 before anyone gets paged.",
            backstory_hint="The last line of defense against a 2am false alarm.",
        ),
        AgentSpec(
            id="filer",
            role="Bug Filer",
            goal="File the bug with its severity label and suggested owner.",
            backstory_hint="Meticulous record-keeper, nothing slips through untracked.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="assess_severity",
            description="Assess how severe the bug is and which part of the system it's likely in, based on the description or stack trace.",
            agent_id="triager",
            expected_output="A severity label (P0-P3) and a suggested owning team.",
        ),
        TaskSpec(
            id="confirm_p0",
            description="If the assessment suspects a P0 (outage, data loss, security), a human must confirm before it goes anywhere.",
            agent_id="confirmer",
            depends_on=["assess_severity"],
            expected_output="A confirmed or downgraded severity for suspected P0s.",
            human_checkpoint=True,
        ),
        TaskSpec(
            id="file_bug",
            description="File the bug with its final severity label and suggested owner for the team to pick up.",
            agent_id="filer",
            depends_on=["confirm_p0"],
            expected_output="The bug filed with severity label and suggested owner.",
        ),
    ],
    tools=[],
    flow=Flow(
        pattern="approval",
        edges=[
            ("assess_severity", "confirm_p0"),
            ("confirm_p0", "file_bug"),
        ],
    ),
    assumptions=["Only suspected P0s go through the human checkpoint; everything else files straight through."],
    ambiguities=[],
)

WATCHER_SPEC = WorkflowSpec(
    workflow=Workflow(
        name="Deploy Health Watcher",
        description="Continuously poll production error rates and notify the on-call engineer when something looks wrong.",
        trigger="Runs on a recurring schedule, polling every few minutes.",
        category="dev",
    ),
    agents=[
        AgentSpec(
            id="poller",
            role="Health Poller",
            goal="Poll the production error-rate metric on each pass.",
            backstory_hint="Never misses a scheduled check, no matter how boring.",
        ),
        AgentSpec(
            id="detector",
            role="Anomaly Detector",
            goal="Decide whether the polled error rate looks anomalous.",
            backstory_hint="Skeptical of noise, alert to real signal.",
        ),
        AgentSpec(
            id="notifier",
            role="On-call Notifier",
            goal="Notify the on-call engineer when an anomaly is detected.",
            backstory_hint="Concise pager messages, no false alarms.",
        ),
    ],
    tasks=[
        TaskSpec(
            id="poll_metrics",
            description="Poll the production error-rate metric for this pass.",
            agent_id="poller",
            expected_output="The current error-rate reading.",
        ),
        TaskSpec(
            id="detect_anomaly",
            description="Compare the current reading against normal range and decide if it's anomalous.",
            agent_id="detector",
            depends_on=["poll_metrics"],
            expected_output="An anomaly flag (yes/no) with the reason.",
        ),
        TaskSpec(
            id="notify_oncall",
            description="If an anomaly was detected, notify the on-call engineer.",
            agent_id="notifier",
            depends_on=["detect_anomaly"],
            expected_output="A notification sent to on-call, or nothing if no anomaly.",
        ),
    ],
    tools=[
        ToolSpec(
            id="send_message",
            name="send message",
            purpose="Notify the on-call engineer of a detected anomaly.",
            category="builtin",
            inputs="channel, message",
            outputs="send confirmation",
        ),
    ],
    flow=Flow(
        pattern="watcher",
        edges=[
            ("poll_metrics", "detect_anomaly"),
            ("detect_anomaly", "notify_oncall"),
        ],
    ),
    assumptions=["Polling interval defaults to the harness's --interval flag rather than a spec-declared value."],
    ambiguities=[],
)

GOLDEN_SPECS: dict[str, WorkflowSpec] = {
    "sequential": SEQUENTIAL_SPEC,
    "router": ROUTER_SPEC,
    "report": REPORT_SPEC,
    "approval": APPROVAL_SPEC,
    "watcher": WATCHER_SPEC,
}
