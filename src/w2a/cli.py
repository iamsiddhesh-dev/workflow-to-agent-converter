import json
import os
import subprocess
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from w2a.logging_config import setup_logging
from w2a.pipeline.graph import run_pipeline
from w2a.spec.ambiguity import format_questions, resolve_ambiguities, score
from w2a.spec.model import WorkflowSpec, human_summary
from w2a.spec.translate import translate
from w2a.validate.repair import run_validation

app = typer.Typer(help="w2a — a LangGraph app that writes CrewAI apps.")

_REPO_ROOT = Path(__file__).resolve().parents[2]

_DEMO_WORKFLOWS = [
    {
        "label": "ticket triage (ops)",
        "description_path": _REPO_ROOT / "examples" / "workflows" / "ticket_triage.md",
        "inputs": sorted((_REPO_ROOT / "examples" / "demo_inputs" / "ticket_triage").glob("*.txt")),
        "run_args": ["--once"],
    },
    {
        "label": "PR summary (dev/eng)",
        "description_path": _REPO_ROOT / "examples" / "workflows" / "pr_summary.md",
        "inputs": [_REPO_ROOT / "examples" / "demo_inputs" / "pr_summary" / "pr_5928.diff"],
        "run_args": [],
    },
]


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


@app.command()
def convert(
    source: str = typer.Argument(..., help="Path to a workflow description file, or '-' for stdin."),
    interactive: bool = typer.Option(False, "--interactive", help="Ask clarifying questions live."),
    out: str = typer.Option("generated", "--out", help="Root directory for generated projects."),
) -> None:
    """Convert a plain-language workflow description into a runnable CrewAI project.

    Translate -> clarify-or-proceed -> lint -> select pattern -> render -> LLM
    gap-fill -> write to <out>/<slug>/ with a provenance manifest.
    """
    description = _read_source(source)
    spec = translate(description)
    report = score(spec)

    if report.clarify:
        typer.echo("This description is ambiguous. Open questions:")
        typer.echo(format_questions(report))
        if interactive:
            spec, report, _ = resolve_ambiguities(description, spec, report, ask=typer.prompt, translate_fn=translate)
            if report.clarify:
                typer.echo("\nStill some open questions after clarification; proceeding with recorded assumptions:")
                typer.echo(format_questions(report))
        else:
            typer.echo("\nRe-run with --interactive to answer, or refine the description.")
            raise typer.Exit(code=2)

    typer.echo(human_summary(spec))

    state = run_pipeline(source=description, spec=spec, out_root=out, pre_translated=True)

    for warning in state.get("warnings", []):
        typer.echo(f"warning: {warning}")

    if state["errors"]:
        typer.echo("\nPipeline errors:")
        for error in state["errors"]:
            typer.echo(f"  {error}")
        raise typer.Exit(code=1)

    selection = state["selection"]
    typer.echo(
        f"\nPattern: {selection.pattern} "
        f"({selection.source}, confidence {selection.confidence:.2f}) — {selection.reasoning}"
    )
    for tool in state["manifest"]["tools"]:
        if tool["resolution"] == "builtin":
            typer.echo(f"tool {tool['tool_id']}: resolved to builtin {tool['builtin']}")
        else:
            typer.echo(f"tool {tool['tool_id']}: MOCK_MODE stub ({tool['reason']})")

    result = state["write_result"]
    if result.no_op:
        typer.echo(f"\nProject already up to date: {result.project_dir}")
    else:
        typer.echo(f"\nProject written to {result.project_dir} ({len(result.written)} file(s))")
    typer.echo(f"Run it: cd {result.project_dir} && pip install -r requirements.txt && MOCK_MODE=1 python main.py")
    raise typer.Exit(code=0)


@app.command()
def validate(
    project: str = typer.Argument(..., help="Path to a generated project directory."),
    max_repairs: int = typer.Option(3, "--max-repairs", help="Bounded repair iterations before an honest failure."),
) -> None:
    """Validate a generated project: static -> env -> exec -> specificity, repairing on failure."""
    project_dir = Path(project)
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        typer.echo(f"error: {manifest_path} not found — is this a w2a-generated project?")
        raise typer.Exit(code=2)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = WorkflowSpec.model_validate(manifest["spec"])

    report = run_validation(project_dir, spec, max_iterations=max_repairs)
    typer.echo(str(report))
    typer.echo(f"\nFull report: {project_dir / 'validation_report.json'}")
    raise typer.Exit(code=0 if report.verdict != "fail" else 1)


@app.command()
def demo(
    out: str = typer.Option("generated", "--out", help="Root directory for generated projects."),
) -> None:
    """Convert, validate, and really run the two Field Trial demo workflows end to end.

    ticket_triage (ops) and pr_summary (dev/eng): translate -> generate ->
    validate -> execute against committed sample inputs with a real LLM.
    Requires GEMINI_API_KEY or GROQ_API_KEY.
    """
    any_failed = False
    for workflow in _DEMO_WORKFLOWS:
        typer.echo(f"\n=== {workflow['label']} ===")
        description = workflow["description_path"].read_text(encoding="utf-8")

        typer.echo("-- convert --")
        state = run_pipeline(source=description, out_root=out)
        for warning in state.get("warnings", []):
            typer.echo(f"warning: {warning}")
        if state["errors"]:
            typer.echo("pipeline errors:")
            for error in state["errors"]:
                typer.echo(f"  {error}")
            any_failed = True
            continue

        result = state["write_result"]
        project_dir = result.project_dir
        typer.echo(f"generated -> {project_dir}")

        typer.echo("-- validate --")
        spec = state["spec"]
        report = run_validation(project_dir, spec, max_iterations=3)
        typer.echo(str(report))
        if report.verdict == "fail":
            typer.echo("validation failed — skipping real-mode run.")
            any_failed = True
            continue

        typer.echo("-- real run --")
        env = os.environ.copy()
        env.pop("MOCK_MODE", None)
        for input_path in workflow["inputs"]:
            typer.echo(f"input: {input_path.name}")
            proc = subprocess.run(
                [sys.executable, "main.py", str(input_path), *workflow["run_args"]],
                cwd=project_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            typer.echo(proc.stdout)
            if proc.returncode != 0:
                typer.echo(f"error: main.py exited {proc.returncode}")
                typer.echo(proc.stderr)
                any_failed = True

    raise typer.Exit(code=1 if any_failed else 0)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    setup_logging()
    app()


if __name__ == "__main__":
    main()
