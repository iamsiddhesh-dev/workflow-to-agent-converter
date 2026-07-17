"""Phase 4.3: manifest provenance + idempotent writer. The two acceptance
cases from the plan: regeneration on an identical spec is a no-op diff, and a
foreign (non-w2a) directory is refused.
"""

import json

import pytest

from tests.golden_specs import ROUTER_SPEC
from w2a.generate.gapfill import GapFillReport
from w2a.generate.manifest import MANIFEST_NAME, build_manifest, file_hashes
from w2a.generate.registry import resolve_all
from w2a.generate.writer import WriterError, write_project
from w2a.templates.render import render_pattern
from w2a.templates.selector import SelectionResult

SELECTION = SelectionResult(pattern="router", confidence=1.0, source="deterministic", reasoning="matches")


def _generate():
    resolutions = resolve_all(ROUTER_SPEC)
    files = render_pattern(ROUTER_SPEC, "router", resolutions)
    manifest = build_manifest(
        spec=ROUTER_SPEC,
        selection=SELECTION,
        resolutions=resolutions,
        files=files,
        gapfill_report=GapFillReport(),
        source_description="founder-speak about tickets",
        llm_calls=["translate"],
    )
    return files, manifest


def test_fresh_write_creates_project_and_manifest(tmp_path):
    files, manifest = _generate()
    result = write_project(files, manifest, out_root=tmp_path)
    assert result.project_dir == tmp_path / "support_ticket_triage"
    assert not result.no_op
    assert sorted(result.written) == sorted(files)
    on_disk = json.loads((result.project_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert on_disk["generator"] == "w2a"
    assert on_disk["pattern"]["selected"] == "router"
    assert on_disk["tools"][0] == {
        "tool_id": "send_message",
        "name": "send message",
        "category": "builtin",
        "resolution": "builtin",
        "builtin": "send_message",
    }
    assert on_disk["files"] == file_hashes(files)
    assert on_disk["source_description"] == "founder-speak about tickets"
    assert (result.project_dir / "crew.py").exists()


def test_regeneration_on_identical_files_is_a_no_op(tmp_path):
    files, manifest = _generate()
    write_project(files, manifest, out_root=tmp_path)
    manifest_before = (tmp_path / "support_ticket_triage" / MANIFEST_NAME).read_text(encoding="utf-8")

    files2, manifest2 = _generate()
    result = write_project(files2, manifest2, out_root=tmp_path)
    assert result.no_op
    assert result.written == []
    assert (tmp_path / "support_ticket_triage" / MANIFEST_NAME).read_text(encoding="utf-8") == manifest_before


def test_changed_file_rewrites_only_that_file(tmp_path):
    files, manifest = _generate()
    write_project(files, manifest, out_root=tmp_path)

    files2, manifest2 = _generate()
    files2["README.md"] = files2["README.md"] + "\nextra line\n"
    manifest2["files"] = file_hashes(files2)
    result = write_project(files2, manifest2, out_root=tmp_path)
    assert result.written == ["README.md"]
    assert not result.no_op
    assert "crew.py" in result.unchanged


def test_stale_recorded_file_is_removed_but_unknown_files_kept(tmp_path):
    files, manifest = _generate()
    write_project(files, manifest, out_root=tmp_path)
    project = tmp_path / "support_ticket_triage"
    (project / "user_notes.txt").write_text("mine", encoding="utf-8")

    files2, manifest2 = _generate()
    del files2["README.md"]
    manifest2["files"] = file_hashes(files2)
    result = write_project(files2, manifest2, out_root=tmp_path)
    assert result.removed == ["README.md"]
    assert not (project / "README.md").exists()
    assert (project / "user_notes.txt").exists()


def test_foreign_directory_is_refused(tmp_path):
    foreign = tmp_path / "support_ticket_triage"
    foreign.mkdir()
    (foreign / "precious.py").write_text("do not touch", encoding="utf-8")
    files, manifest = _generate()
    with pytest.raises(WriterError, match="refusing to overwrite"):
        write_project(files, manifest, out_root=tmp_path)
    assert (foreign / "precious.py").read_text(encoding="utf-8") == "do not touch"


def test_non_w2a_manifest_is_refused(tmp_path):
    foreign = tmp_path / "support_ticket_triage"
    foreign.mkdir()
    (foreign / MANIFEST_NAME).write_text(json.dumps({"generator": "other-tool"}), encoding="utf-8")
    files, manifest = _generate()
    with pytest.raises(WriterError, match="not written by w2a"):
        write_project(files, manifest, out_root=tmp_path)


def test_empty_existing_directory_is_fine(tmp_path):
    (tmp_path / "support_ticket_triage").mkdir()
    files, manifest = _generate()
    result = write_project(files, manifest, out_root=tmp_path)
    assert sorted(result.written) == sorted(files)
