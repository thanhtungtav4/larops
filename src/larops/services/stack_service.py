from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Callable, Iterable

from larops.core.shell import run_command


class StackServiceError(RuntimeError):
    pass


DEBIAN_PACKAGE_GROUPS = {
    "data": ["mariadb-server", "redis-server"],
    "postgres": ["postgresql"],
    "ops": ["fail2ban", "ufw"],
}

EL9_COMMON_PACKAGE_GROUPS = {
    "web": [
        "nginx",
        "certbot",
        "composer",
        "nodejs",
        "php-fpm",
        "php-cli",
        "php-mbstring",
        "php-mysqlnd",
        "php-pgsql",
        "php-xml",
        "php-curl",
        "php-zip",
    ],
    "data": ["mariadb-server", "redis"],
    "postgres": ["postgresql-server"],
}


SUPPORTED_STACK_PLATFORMS = (
    {"os_ids": {"ubuntu"}, "version_prefixes": ("22.04",), "family": "debian", "package_manager": "apt", "support_level": "ga"},
    {"os_ids": {"ubuntu"}, "version_prefixes": ("24.04",), "family": "debian", "package_manager": "apt", "support_level": "ga"},
    {"os_ids": {"debian"}, "version_prefixes": ("12",), "family": "debian", "package_manager": "apt", "support_level": "ga"},
    {"os_ids": {"debian"}, "version_prefixes": ("13",), "family": "debian", "package_manager": "apt", "support_level": "experimental"},
    {"os_ids": {"rocky", "almalinux"}, "version_prefixes": ("9",), "family": "el9", "package_manager": "dnf", "support_level": "experimental"},
    {"os_ids": {"rhel"}, "version_prefixes": ("9",), "family": "el9", "package_manager": "dnf", "support_level": "experimental"},
)


@dataclass(slots=True)
class StackPlatform:
    os_id: str
    version_id: str
    family: str
    package_manager: str
    support_level: str

    @property
    def label(self) -> str:
        return f"{self.os_id} {self.version_id}".strip()


@dataclass(slots=True)
class StackPlan:
    groups: list[str]
    commands: list[list[str]]
    platform: StackPlatform
    php_version: str | None = None
    php_repo_provider: str | None = None


def _parse_os_release(path: Path) -> dict[str, str]:
    if not path.exists():
        raise StackServiceError(f"os-release file not found: {path}")
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key] = value.strip().strip('"')
    return payload


def _platform_definition(os_id: str, version_id: str) -> dict[str, object] | None:
    for item in SUPPORTED_STACK_PLATFORMS:
        if os_id not in item["os_ids"]:
            continue
        if any(version_id.startswith(prefix) for prefix in item["version_prefixes"]):
            return item
    return None


def _supported_platform_summary() -> str:
    summary: list[str] = []
    for item in SUPPORTED_STACK_PLATFORMS:
        os_ids = "/".join(sorted(str(os_id) for os_id in item["os_ids"]))
        versions = "/".join(str(prefix) for prefix in item["version_prefixes"])
        summary.append(f"{os_ids} {versions}")
    return ", ".join(summary)


def package_groups_for_platform(platform: StackPlatform) -> dict[str, list[str]]:
    if platform.family == "debian":
        return {key: list(value) for key, value in DEBIAN_PACKAGE_GROUPS.items()}
    if platform.family == "el9":
        groups = {key: list(value) for key, value in EL9_COMMON_PACKAGE_GROUPS.items()}
        if platform.os_id in {"rocky", "almalinux", "rhel"}:
            groups["ops"] = ["epel-release", "fail2ban", "firewalld"]
        else:
            groups["ops"] = ["firewalld"]
        return groups
    raise StackServiceError(f"Unsupported platform family: {platform.family}")


def _normalize_php_version(php_version: str | None) -> str | None:
    if php_version is None:
        return None
    normalized = php_version.strip()
    if not normalized:
        return None
    if not re.fullmatch(r"\d+\.\d+", normalized):
        raise StackServiceError(f"Unsupported PHP version format: {php_version}. Use major.minor, for example 8.3.")
    return normalized


def _debian_web_packages(php_version: str) -> list[str]:
    return [
        "nginx",
        "certbot",
        "composer",
        "nodejs",
        "npm",
        f"php{php_version}-fpm",
        f"php{php_version}-cli",
        f"php{php_version}-mbstring",
        f"php{php_version}-mysql",
        f"php{php_version}-pgsql",
        f"php{php_version}-xml",
        f"php{php_version}-curl",
        f"php{php_version}-zip",
    ]


def _php_repo_provider_for_platform(*, platform: StackPlatform, php_version: str | None) -> str | None:
    if platform.family != "debian":
        return None
    if php_version is None or php_version == "8.3":
        return None
    if platform.os_id == "ubuntu":
        return "ondrej"
    if platform.os_id == "debian":
        return "sury"
    return None


def _ubuntu_php_repo_commands() -> list[list[str]]:
    return [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "software-properties-common", "ca-certificates", "lsb-release", "apt-transport-https"],
        ["add-apt-repository", "-y", "ppa:ondrej/php"],
        ["apt-get", "update"],
    ]


def _debian_php_repo_commands() -> list[list[str]]:
    return [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "ca-certificates", "curl", "apt-transport-https", "gnupg2", "lsb-release"],
        ["curl", "-fsSLo", "/tmp/debsuryorg-archive-keyring.deb", "https://packages.sury.org/debsuryorg-archive-keyring.deb"],
        ["dpkg", "-i", "/tmp/debsuryorg-archive-keyring.deb"],
        [
            "bash",
            "-lc",
            'echo "deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ $(. /etc/os-release && echo ${VERSION_CODENAME}) main" > /etc/apt/sources.list.d/php.list',
        ],
        ["apt-get", "update"],
    ]


def php_repo_setup_commands(*, platform: StackPlatform, php_version: str | None) -> tuple[str | None, list[list[str]]]:
    provider = _php_repo_provider_for_platform(platform=platform, php_version=php_version)
    if provider is None:
        return None, []
    if provider == "ondrej":
        return provider, _ubuntu_php_repo_commands()
    if provider == "sury":
        return provider, _debian_php_repo_commands()
    raise StackServiceError(f"Unsupported PHP repo provider: {provider}")


def _resolve_os_release_path(path: Path | None) -> Path:
    if path is not None:
        return path
    env_override = os.getenv("LAROPS_STACK_OS_RELEASE_PATH", "").strip()
    if env_override:
        return Path(env_override)
    return Path("/etc/os-release")


def detect_stack_platform(*, os_release_path: Path | None = None) -> StackPlatform:
    resolved_os_release_path = _resolve_os_release_path(os_release_path)
    payload = _parse_os_release(resolved_os_release_path)
    os_id = payload.get("ID", "").strip().lower()
    version_id = payload.get("VERSION_ID", "").strip().lower()
    if not os_id or not version_id:
        raise StackServiceError(f"Unable to determine OS from {resolved_os_release_path}")
    platform_data = _platform_definition(os_id, version_id)
    if platform_data is None:
        raise StackServiceError(
            f"Unsupported host OS for stack install: {os_id} {version_id}. Supported: {_supported_platform_summary()}."
        )
    return StackPlatform(
        os_id=os_id,
        version_id=version_id,
        family=str(platform_data["family"]),
        package_manager=str(platform_data["package_manager"]),
        support_level=str(platform_data["support_level"]),
    )


def resolve_groups(web: bool, data: bool, postgres: bool, ops: bool) -> list[str]:
    return [name for name, enabled in {"web": web, "data": data, "postgres": postgres, "ops": ops}.items() if enabled]


def build_install_commands(
    groups: Iterable[str],
    *,
    platform: StackPlatform,
    php_version: str | None = None,
) -> list[list[str]]:
    normalized_php_version = _normalize_php_version(php_version)
    package_groups = package_groups_for_platform(platform)
    php_repo_provider, php_repo_commands = php_repo_setup_commands(platform=platform, php_version=normalized_php_version)
    if "web" in groups:
        if platform.family == "debian":
            package_groups["web"] = _debian_web_packages(normalized_php_version or "8.3")
        elif normalized_php_version is not None:
            raise StackServiceError(
                f"Explicit PHP version pinning is not supported on {platform.label}. "
                "Use the distro default PHP stream on this preview platform."
            )
    packages: list[str] = []
    for group in groups:
        packages.extend(package_groups[group])
    dedup_packages = sorted(set(packages))

    if platform.package_manager == "apt":
        if php_repo_provider is not None:
            return [*php_repo_commands, ["apt-get", "install", "-y", *dedup_packages]]
        return [["apt-get", "update"], ["apt-get", "install", "-y", *dedup_packages]]

    if platform.package_manager == "dnf":
        commands: list[list[str]] = [["dnf", "makecache", "-y"]]
        if "epel-release" in dedup_packages:
            commands.append(["dnf", "install", "-y", "epel-release"])
            commands.append(["dnf", "makecache", "-y"])
            dedup_packages = [package for package in dedup_packages if package != "epel-release"]
        if dedup_packages:
            commands.append(["dnf", "install", "-y", *dedup_packages])
        return commands

    raise StackServiceError(f"Unsupported package manager for stack install: {platform.package_manager}")


def build_stack_plan(
    groups: list[str],
    *,
    os_release_path: Path | None = None,
    php_version: str | None = None,
) -> StackPlan:
    platform = detect_stack_platform(os_release_path=os_release_path)
    normalized_php_version = _normalize_php_version(php_version)
    php_repo_provider, _ = php_repo_setup_commands(platform=platform, php_version=normalized_php_version)
    return StackPlan(
        groups=groups,
        commands=build_install_commands(groups, platform=platform, php_version=normalized_php_version),
        platform=platform,
        php_version=normalized_php_version if "web" in groups else None,
        php_repo_provider=php_repo_provider if "web" in groups else None,
    )


def apply_stack_plan(
    plan: StackPlan,
    *,
    on_command_start: Callable[[list[str]], None] | None = None,
    on_command_complete: Callable[[list[str]], None] | None = None,
) -> None:
    for command in plan.commands:
        if on_command_start is not None:
            on_command_start(command)
        run_command(command, check=True)
        if on_command_complete is not None:
            on_command_complete(command)
