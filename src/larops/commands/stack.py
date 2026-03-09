import typer
import socket

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError
from larops.models import EventRecord
from larops.runtime import AppContext
from larops.services.stack_service import StackServiceError, apply_stack_plan, build_stack_plan, resolve_groups

stack_app = typer.Typer(help="Manage host stack components.")


@stack_app.command("install")
def install(
    ctx: typer.Context,
    web: bool = typer.Option(False, "--web", help="Install web runtime components."),
    data: bool = typer.Option(False, "--data", help="Install data components."),
    postgres: bool = typer.Option(False, "--postgres", help="Install PostgreSQL components."),
    ops: bool = typer.Option(False, "--ops", help="Install operations components."),
    php: str | None = typer.Option(None, "--php", help="PHP runtime version for the web stack, for example 8.3 or 8.4."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply package changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    host = socket.gethostname()
    requested = resolve_groups(web, data, postgres, ops)
    if not requested:
        app_ctx.emit_output("error", "No stack group selected. Use --web, --data, --postgres, or --ops.")
        raise typer.Exit(code=2)

    try:
        plan = build_stack_plan(requested, php_version=php)
    except StackServiceError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=2) from exc
    app_ctx.event_emitter.emit(
        EventRecord(
            severity="info",
            event_type="stack.install.started",
            host=host,
            message="Stack installation started.",
            metadata={
                "groups": requested,
                "platform": plan.platform.label,
                "support_level": plan.platform.support_level,
                "php_version": plan.php_version,
                "php_repo_provider": plan.php_repo_provider,
                "apply": apply,
                "dry_run": app_ctx.dry_run,
            },
        )
    )

    app_ctx.emit_output(
        "ok",
        f"Stack plan prepared for groups: {', '.join(requested)}",
        groups=requested,
        platform=plan.platform.label,
        support_level=plan.platform.support_level,
        php_version=plan.php_version,
        php_repo_provider=plan.php_repo_provider,
        commands=plan.commands,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("stack-install"):
            apply_stack_plan(
                plan,
                on_command_start=lambda command: app_ctx.emit_output("ok", f"Running: {' '.join(command)}"),
                on_command_complete=lambda command: app_ctx.emit_output("ok", f"Executed: {' '.join(command)}"),
            )
    except CommandLockError as exc:
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=5) from exc
    except ShellCommandError as exc:
        app_ctx.event_emitter.emit(
            EventRecord(
                severity="error",
                event_type="stack.install.failed",
                host=host,
                message="Stack installation failed.",
                metadata={"error": str(exc)},
            )
        )
        app_ctx.emit_output("error", str(exc))
        raise typer.Exit(code=1) from exc

    app_ctx.event_emitter.emit(
        EventRecord(
            severity="info",
            event_type="stack.install.completed",
            host=host,
            message="Stack installation completed.",
            metadata={"groups": requested},
        )
    )
    app_ctx.emit_output("ok", "Stack installation completed.")
