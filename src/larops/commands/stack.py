import typer
import socket

from larops.core.locks import CommandLock, CommandLockError
from larops.core.shell import ShellCommandError, run_command
from larops.models import EventRecord
from larops.runtime import AppContext

stack_app = typer.Typer(help="Manage host stack components.")

PACKAGE_GROUPS = {
    "web": [
        "nginx",
        "php8.3-fpm",
        "php8.3-cli",
        "php8.3-mbstring",
        "php8.3-xml",
        "php8.3-curl",
        "php8.3-zip",
    ],
    "data": ["mariadb-server", "redis-server"],
    "ops": ["supervisor", "fail2ban", "ufw"],
}


def resolve_groups(web: bool, data: bool, ops: bool) -> list[str]:
    return [name for name, enabled in {"web": web, "data": data, "ops": ops}.items() if enabled]


def build_install_commands(groups: list[str]) -> list[list[str]]:
    packages: list[str] = []
    for group in groups:
        packages.extend(PACKAGE_GROUPS[group])
    dedup_packages = sorted(set(packages))
    return [["apt-get", "update"], ["apt-get", "install", "-y", *dedup_packages]]


@stack_app.command("install")
def install(
    ctx: typer.Context,
    web: bool = typer.Option(False, "--web", help="Install web runtime components."),
    data: bool = typer.Option(False, "--data", help="Install data components."),
    ops: bool = typer.Option(False, "--ops", help="Install operations components."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply package changes. Without this flag, command runs in plan mode.",
    ),
) -> None:
    app_ctx: AppContext = ctx.obj
    host = socket.gethostname()
    requested = resolve_groups(web, data, ops)
    if not requested:
        app_ctx.emit_output("error", "No stack group selected. Use --web, --data, or --ops.")
        raise typer.Exit(code=2)

    planned_commands = build_install_commands(requested)
    app_ctx.event_emitter.emit(
        EventRecord(
            severity="info",
            event_type="stack.install.started",
            host=host,
            message="Stack installation started.",
            metadata={"groups": requested, "apply": apply, "dry_run": app_ctx.dry_run},
        )
    )

    app_ctx.emit_output(
        "ok",
        f"Stack plan prepared for groups: {', '.join(requested)}",
        groups=requested,
        commands=planned_commands,
        apply=apply,
        dry_run=app_ctx.dry_run,
    )

    if app_ctx.dry_run or not apply:
        app_ctx.emit_output("ok", "Plan mode finished. Use --apply to execute changes.")
        return

    try:
        with CommandLock("stack-install"):
            for command in planned_commands:
                run_command(command, check=True)
                app_ctx.emit_output("ok", f"Executed: {' '.join(command)}")
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
