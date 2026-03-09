from __future__ import annotations

from pathlib import Path

from larops.services.stack_service import StackServiceError, detect_stack_platform


def detected_platform_family() -> str:
    try:
        return detect_stack_platform().family
    except StackServiceError:
        return "debian"


def default_fail2ban_jail_file() -> Path:
    return Path("/etc/fail2ban/jail.d/larops.conf")


def default_fail2ban_filter_file() -> Path:
    return Path("/etc/fail2ban/filter.d/larops-nginx-scan.conf")


def default_fail2ban_log_path() -> Path:
    return Path("/var/log/fail2ban.log")


def default_nginx_access_log_path() -> Path:
    family = detected_platform_family()
    if family in {"debian", "el9"}:
        return Path("/var/log/nginx/access.log")
    return Path("/var/log/nginx/access.log")


def default_nginx_error_log_path() -> Path:
    family = detected_platform_family()
    if family in {"debian", "el9"}:
        return Path("/var/log/nginx/error.log")
    return Path("/var/log/nginx/error.log")


def default_nginx_access_logs() -> list[str]:
    return [str(default_nginx_access_log_path())]


def default_nginx_error_logs() -> list[str]:
    return [str(default_nginx_error_log_path())]
