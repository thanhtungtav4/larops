import typer

stack_app = typer.Typer(help="Manage host stack components.")


@stack_app.command("install")
def install(
    web: bool = typer.Option(False, "--web", help="Install web runtime components."),
    data: bool = typer.Option(False, "--data", help="Install data components."),
    ops: bool = typer.Option(False, "--ops", help="Install operations components."),
) -> None:
    requested = [name for name, enabled in {"web": web, "data": data, "ops": ops}.items() if enabled]
    if not requested:
        typer.echo("No stack group selected. Use --web, --data, or --ops.")
        raise typer.Exit(code=2)

    typer.echo(f"[bootstrap] stack install requested for: {', '.join(requested)}")
    typer.echo("Implementation will be added in S2.")

