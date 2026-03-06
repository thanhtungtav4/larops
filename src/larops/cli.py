from pathlib import Path

import typer

from larops import __version__
from larops.commands.alert import alert_app
from larops.commands.app import app_cmd
from larops.commands.bootstrap import bootstrap_app
from larops.commands.create import create_app
from larops.commands.db import db_app
from larops.commands.doctor import doctor_app
from larops.commands.horizon import horizon_app
from larops.commands.monitor import monitor_app
from larops.commands.notify import notify_app
from larops.commands.observability import observability_app
from larops.commands.scheduler import scheduler_app
from larops.commands.security import security_app
from larops.commands.site import site_app
from larops.commands.ssl import ssl_app
from larops.commands.stack import stack_app
from larops.commands.worker import worker_app
from larops.config import ConfigError, load_config
from larops.runtime import AppContext

app = typer.Typer(help="LarOps: Laravel-first server operations CLI.")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
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
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions only."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable verbose logs."),
) -> None:
    _ = version
    try:
        loaded = load_config(config)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}")
        raise typer.Exit(code=2) from exc
    ctx.obj = AppContext.from_config(
        loaded,
        config_path=config,
        json_output=json_output,
        dry_run=dry_run,
        verbose=verbose,
    )


app.add_typer(stack_app, name="stack")
app.add_typer(create_app, name="create")
app.add_typer(site_app, name="site")
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(app_cmd, name="app")
app.add_typer(worker_app, name="worker")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(horizon_app, name="horizon")
app.add_typer(ssl_app, name="ssl")
app.add_typer(db_app, name="db")
app.add_typer(notify_app, name="notify")
app.add_typer(observability_app, name="observability")
app.add_typer(doctor_app, name="doctor")
app.add_typer(security_app, name="security")
app.add_typer(alert_app, name="alert")
app.add_typer(monitor_app, name="monitor")
