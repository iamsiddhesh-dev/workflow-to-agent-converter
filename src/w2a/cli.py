import typer
from dotenv import load_dotenv

from w2a.logging_config import setup_logging

app = typer.Typer(help="w2a — a LangGraph app that writes CrewAI apps.")


@app.command()
def convert(
    source: str = typer.Argument(..., help="Path to a workflow description file, or '-' for stdin."),
    interactive: bool = typer.Option(False, "--interactive", help="Ask clarifying questions live."),
) -> None:
    """Convert a plain-language workflow description into a runnable CrewAI project."""
    typer.echo(f"[stub] convert: source={source} interactive={interactive}")
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
