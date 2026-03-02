import typer

doctor_app = typer.Typer(help="Run health checks.")


@doctor_app.command("run")
def run(
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    typer.echo(f"[bootstrap] doctor run target={target}")
    typer.echo("Implementation will be added in S5.")

