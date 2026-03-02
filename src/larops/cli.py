from pathlib import Path

import typer

from larops import __version__
from larops.commands.app import app_cmd
from larops.commands.doctor import doctor_app
from larops.commands.stack import stack_app
from larops.config import load_config

app = typer.Typer(help="LarOps: Laravel-first server operations CLI.")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to configuration file.",
        exists=False,
        readable=True,
        dir_okay=False,
    ),
) -> None:
    _ = version
    _ = load_config(config)


app.add_typer(stack_app, name="stack")
app.add_typer(app_cmd, name="app")
app.add_typer(doctor_app, name="doctor")

