import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from w2a.logging_config import setup_logging
from w2a.spec.ambiguity import format_questions, score
from w2a.spec.lint import lint
from w2a.spec.model import human_summary
from w2a.spec.translate import translate

app = typer.Typer(help="w2a — a LangGraph app that writes CrewAI apps.")


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


@app.command()
def convert(
    source: str = typer.Argument(..., help="Path to a workflow description file, or '-' for stdin."),
    interactive: bool = typer.Option(False, "--interactive", help="Ask clarifying questions live."),
) -> None:
    """Convert a plain-language workflow description into a WorkflowSpec.

    Phase 2 stops at the spec: translate, lint, and route ambiguities to clarify
    mode or proceed-with-assumptions. Project generation lands in Phase 4.
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
    errors = [i for i in lint(spec) if i.severity == "error"]
    warnings = [i for i in lint(spec) if i.severity == "warning"]
    if warnings:
        typer.echo("\nLint warnings:")
        for w in warnings:
            typer.echo(f"  {w}")
    if errors:
        typer.echo("\nLint errors:")
        for e in errors:
            typer.echo(f"  {e}")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def validate(
    project: str = typer.Argument(..., help="Path to a generated project directory."),
) -> None:
    """Validate a generated project (static, environment, execution, specificity tiers)."""
    typer.echo(f"[stub] validate: project={project}")
    raise typer.Exit(code=0)


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
