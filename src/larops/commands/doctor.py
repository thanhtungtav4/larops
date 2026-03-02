import typer

from larops.runtime import AppContext

doctor_app = typer.Typer(help="Run health checks.")


@doctor_app.command("run")
def run(
    ctx: typer.Context,
    target: str = typer.Argument("host", help="Host or app identifier."),
) -> None:
    app_ctx: AppContext = ctx.obj
    app_ctx.emit_output("ok", f"[bootstrap] doctor run target={target}")
    app_ctx.emit_output("ok", "Implementation will be added in S5.")
