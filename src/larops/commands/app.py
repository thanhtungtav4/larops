import typer

app_cmd = typer.Typer(help="Manage Laravel application lifecycle.")


@app_cmd.command("create")
def create(
    domain: str = typer.Argument(..., help="Application domain."),
    php: str = typer.Option("8.3", "--php", help="PHP runtime version."),
    db: str = typer.Option("mysql", "--db", help="Database engine."),
    ssl: bool = typer.Option(False, "--ssl", help="Issue SSL certificate."),
) -> None:
    typer.echo(
        f"[bootstrap] app create target={domain} php={php} db={db} ssl={'yes' if ssl else 'no'}"
    )
    typer.echo("Implementation will be added in S3.")


@app_cmd.command("deploy")
def deploy(
    domain: str = typer.Argument(..., help="Application domain."),
    ref: str = typer.Option("main", "--ref", help="Git ref to deploy."),
) -> None:
    typer.echo(f"[bootstrap] app deploy target={domain} ref={ref}")
    typer.echo("Implementation will be added in S3.")


@app_cmd.command("rollback")
def rollback(
    domain: str = typer.Argument(..., help="Application domain."),
    to: str = typer.Option("previous", "--to", help="Release id or 'previous'."),
) -> None:
    typer.echo(f"[bootstrap] app rollback target={domain} to={to}")
    typer.echo("Implementation will be added in S3.")

