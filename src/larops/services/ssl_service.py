from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from larops.core.shell import run_command


class SslServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class CertificateInfo:
    cert_file: Path
    subject: str
    issuer: str
    not_after: datetime
    days_remaining: int


def default_cert_file(domain: str) -> Path:
    return Path("/etc/letsencrypt/live") / domain / "fullchain.pem"


def build_issue_command(
    *,
    domain: str,
    email: str | None,
    challenge: str,
    dns_provider: str | None,
    staging: bool,
) -> list[str]:
    command = [
        "certbot",
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "-d",
        domain,
    ]
    if email:
        command += ["--email", email]
    else:
        command.append("--register-unsafely-without-email")
    if challenge == "http":
        command += ["--webroot", "-w", "/var/www/html"]
    elif challenge == "dns":
        if not dns_provider:
            raise SslServiceError("dns challenge requires --dns-provider")
        command += [f"--dns-{dns_provider}"]
    else:
        raise SslServiceError(f"Unsupported challenge type: {challenge}")
    if staging:
        command.append("--staging")
    return command


def run_issue(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()


def build_renew_command(*, force: bool, dry_run: bool) -> list[str]:
    command = ["certbot", "renew"]
    if force:
        command.append("--force-renewal")
    if dry_run:
        command.append("--dry-run")
    return command


def run_renew(command: list[str]) -> str:
    completed = run_command(command, check=True)
    return (completed.stdout or "").strip()


def read_certificate_info(cert_file: Path) -> CertificateInfo:
    if not cert_file.exists():
        raise SslServiceError(f"Certificate file not found: {cert_file}")

    end = run_command(["openssl", "x509", "-in", str(cert_file), "-noout", "-enddate"], check=True)
    subject = run_command(["openssl", "x509", "-in", str(cert_file), "-noout", "-subject"], check=True)
    issuer = run_command(["openssl", "x509", "-in", str(cert_file), "-noout", "-issuer"], check=True)

    line = (end.stdout or "").strip()
    if not line.startswith("notAfter="):
        raise SslServiceError(f"Unexpected openssl output: {line}")
    not_after_str = line.split("=", 1)[1].strip()
    not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    now = datetime.now(UTC)
    days_remaining = int((not_after - now).total_seconds() // 86400)

    return CertificateInfo(
        cert_file=cert_file,
        subject=(subject.stdout or "").strip(),
        issuer=(issuer.stdout or "").strip(),
        not_after=not_after,
        days_remaining=days_remaining,
    )

