"""Idempotent project writer for ``generated/<slug>/``.

Ownership rule: the writer only ever writes into an empty/new directory or one
carrying its own ``manifest.json`` with ``generator: "w2a"`` — anything else is
refused, so a user's hand-made directory can never be clobbered by a slug
collision. Idempotency rule: regeneration of byte-identical files is a no-op
(nothing rewritten, manifest untouched); otherwise only changed files are
written and files recorded in the previous manifest but no longer generated
are removed. Files the writer never recorded are never deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from w2a.generate.manifest import GENERATOR, MANIFEST_NAME, file_hashes


class WriterError(Exception):
    """Raised when the target directory can't be safely written."""


@dataclass
class WriteResult:
    project_dir: Path
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    no_op: bool = False


def _load_previous_manifest(target: Path) -> dict | None:
    manifest_path = target / MANIFEST_NAME
    if not target.exists():
        return None
    if not manifest_path.exists():
        if any(target.iterdir()):
            raise WriterError(
                f"{target} exists and is not a w2a-generated project (no {MANIFEST_NAME}) — refusing to overwrite."
            )
        return None
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriterError(f"{manifest_path} is unreadable ({exc}) — refusing to overwrite.") from exc
    if previous.get("generator") != GENERATOR:
        raise WriterError(f"{manifest_path} was not written by w2a — refusing to overwrite.")
    return previous


def write_project(files: dict[str, str], manifest: dict, out_root: str | Path = "generated") -> WriteResult:
    """Write a generated project to ``out_root/<slug>/``. Returns what actually changed."""
    slug = manifest["workflow"]["slug"]
    target = Path(out_root) / slug
    previous = _load_previous_manifest(target)
    result = WriteResult(project_dir=target)

    new_hashes = file_hashes(files)
    previous_hashes: dict[str, str] = (previous or {}).get("files", {})
    if previous is not None and previous_hashes == new_hashes:
        result.unchanged = sorted(files)
        result.no_op = True
        return result

    target.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(files.items()):
        if previous_hashes.get(name) == new_hashes[name]:
            result.unchanged.append(name)
            continue
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        result.written.append(name)

    for stale in sorted(set(previous_hashes) - set(files)):
        stale_path = target / stale
        if stale_path.exists():
            stale_path.unlink()
            result.removed.append(stale)

    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return result
