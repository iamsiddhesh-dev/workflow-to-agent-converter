"""Generation manifest: full provenance for one generated project.

The manifest is both the demo artifact ("here is exactly what was decided and
why") and the writer's ownership marker — ``writer.py`` refuses to touch a
directory that doesn't carry one, and uses the recorded file hashes to make
regeneration idempotent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from w2a.spec.model import WorkflowSpec
from w2a.templates.render import _slugify

MANIFEST_NAME = "manifest.json"
GENERATOR = "w2a"
MANIFEST_VERSION = 1


def file_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def file_hashes(files: dict[str, str]) -> dict[str, str]:
    return {name: file_sha256(content) for name, content in sorted(files.items())}


def build_manifest(
    *,
    spec: WorkflowSpec,
    selection,
    resolutions: dict,
    files: dict[str, str],
    gapfill_report=None,
    source_description: str | None = None,
    llm_calls: list[str] | None = None,
) -> dict:
    tools = []
    for tool in spec.tools:
        res = resolutions.get(tool.id)
        entry = {"tool_id": tool.id, "name": tool.name, "category": tool.category}
        if getattr(res, "source", None) is not None:
            entry["resolution"] = "builtin"
            entry["builtin"] = res.name
        else:
            entry["resolution"] = "stub"
            entry["reason"] = getattr(res, "reason", "no resolution attempted")
        tools.append(entry)

    gap_fill = None
    if gapfill_report is not None:
        gap_fill = {
            "llm_called": gapfill_report.llm_called,
            "applied": gapfill_report.applied,
            "rejected": gapfill_report.rejected,
            "error": gapfill_report.error,
        }

    return {
        "generator": GENERATOR,
        "manifest_version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow": {
            "name": spec.workflow.name,
            "slug": _slugify(spec.workflow.name),
            "category": spec.workflow.category,
            "pattern_declared": spec.flow.pattern,
        },
        "pattern": {
            "selected": selection.pattern,
            "confidence": selection.confidence,
            "source": selection.source,
            "reasoning": selection.reasoning,
        },
        "tools": tools,
        "assumptions": list(spec.assumptions),
        "ambiguities": list(spec.ambiguities),
        "gap_fill": gap_fill,
        "llm_calls": llm_calls or [],
        "source_description": source_description,
        "spec": spec.model_dump(),
        "files": file_hashes(files),
    }
