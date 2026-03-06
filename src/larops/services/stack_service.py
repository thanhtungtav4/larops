from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from larops.core.shell import run_command


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
    "postgres": ["postgresql"],
    "ops": ["fail2ban", "ufw"],
}


@dataclass(slots=True)
class StackPlan:
    groups: list[str]
    commands: list[list[str]]


def resolve_groups(web: bool, data: bool, postgres: bool, ops: bool) -> list[str]:
    return [name for name, enabled in {"web": web, "data": data, "postgres": postgres, "ops": ops}.items() if enabled]


def build_install_commands(groups: Iterable[str]) -> list[list[str]]:
    packages: list[str] = []
    for group in groups:
        packages.extend(PACKAGE_GROUPS[group])
    dedup_packages = sorted(set(packages))
    return [["apt-get", "update"], ["apt-get", "install", "-y", *dedup_packages]]


def build_stack_plan(groups: list[str]) -> StackPlan:
    return StackPlan(groups=groups, commands=build_install_commands(groups))


def apply_stack_plan(plan: StackPlan) -> None:
    for command in plan.commands:
        run_command(command, check=True)
