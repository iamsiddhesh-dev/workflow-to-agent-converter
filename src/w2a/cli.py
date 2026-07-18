import json
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from w2a.logging_config import setup_logging
from w2a.pipeline.graph import run_pipeline
from w2a.spec.ambiguity import format_questions, score
from w2a.spec.model import WorkflowSpec, human_summary
from w2a.spec.translate import translate
from w2a.validate.repair import run_validation

app = typer.Typer(help="w2a — a LangGraph app that writes CrewAI apps.")


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
            answers = []
            for q, _ in sorted(report.scored, key=lambda qs: qs[1], reverse=True):
                answers.append(f"Q: {q}\nA: {typer.prompt(q)}")
            spec = translate(description, extra_context="\n".join(answers))
            report = score(spec)
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
def demo() -> None:
    """Run the two real-mode Field Trial workflows start to finish."""
    typer.echo("[stub] demo")
    raise typer.Exit(code=0)


def main() -> None:
    load_dotenv()
    setup_logging()
    app()


if __name__ == "__main__":
    main()
