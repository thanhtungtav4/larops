from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from larops.config import BackupEncryptionConfig, BackupOffsiteConfig
from larops.core.shell import ShellCommandError, run_command
from larops.services.db_service import restore_verify_backup, verify_backup


class DbOffsiteError(RuntimeError):
    pass


_ALLOWED_PROVIDERS = {"s3"}
_ALLOWED_CIPHERS = {"aes-256-cbc"}


@contextmanager
def _passphrase_file(passphrase: str) -> Iterator[Path]:
    fd, path_raw = tempfile.mkstemp(prefix="larops-backup-passphrase-")
    path = Path(path_raw)
    try:
        os.write(fd, passphrase.encode("utf-8"))
        os.close(fd)
        os.chmod(path, 0o600)
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextmanager
def _temporary_dir(prefix: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp_dir:
        yield Path(tmp_dir)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_encryption_config(config: BackupEncryptionConfig) -> None:
    if not config.enabled:
        raise DbOffsiteError("Backup encryption is disabled. Enable backups.encryption before offsite upload.")
    if not config.passphrase:
        raise DbOffsiteError("Backup encryption passphrase is empty.")
    cipher = config.cipher.strip().lower()
    if cipher not in _ALLOWED_CIPHERS:
        allowed = ", ".join(sorted(_ALLOWED_CIPHERS))
        raise DbOffsiteError(f"Unsupported encryption cipher: {config.cipher}. Supported: {allowed}.")


def _validate_offsite_config(config: BackupOffsiteConfig) -> None:
    if not config.enabled:
        raise DbOffsiteError("Offsite backup is disabled. Enable backups.offsite before upload.")
    provider = config.provider.strip().lower()
    if provider not in _ALLOWED_PROVIDERS:
        allowed = ", ".join(sorted(_ALLOWED_PROVIDERS))
        raise DbOffsiteError(f"Unsupported offsite provider: {config.provider}. Supported: {allowed}.")
    if not config.bucket.strip():
        raise DbOffsiteError("Offsite bucket is required.")
    if not config.access_key_id.strip():
        raise DbOffsiteError("Offsite access key id is required.")
    if not config.secret_access_key.strip():
        raise DbOffsiteError("Offsite secret access key is required.")
    if config.retention_days < 1:
        raise DbOffsiteError("Offsite retention_days must be >= 1.")


def _client(config: BackupOffsiteConfig):
    _validate_offsite_config(config)
    try:
        import boto3
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise DbOffsiteError("boto3 is required for offsite S3 storage. Install project dependencies.") from exc
    session = boto3.session.Session(
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        region_name=config.region,
    )
    return session.client("s3", endpoint_url=config.endpoint_url or None)


def offsite_object_prefix(*, offsite_config: BackupOffsiteConfig, domain: str) -> str:
    prefix = offsite_config.prefix.strip("/")
    safe_domain = domain.replace("..", ".").strip("/")
    if prefix:
        return f"{prefix}/{safe_domain}"
    return safe_domain


def encrypted_backup_path(backup_file: Path) -> Path:
    return backup_file.with_name(f"{backup_file.name}.enc")


def encrypted_manifest_path(encrypted_file: Path) -> Path:
    return encrypted_file.with_name(f"{encrypted_file.name}.json")


def encrypt_backup_artifact(*, backup_file: Path, encryption_config: BackupEncryptionConfig) -> tuple[Path, Path]:
    _validate_encryption_config(encryption_config)
    if not backup_file.exists():
        raise DbOffsiteError(f"Backup file not found: {backup_file}")

    encrypted_file = encrypted_backup_path(backup_file)
    with _passphrase_file(encryption_config.passphrase) as passphrase_path:
        try:
            run_command(
                [
                    "openssl",
                    "enc",
                    f"-{encryption_config.cipher}",
                    "-pbkdf2",
                    "-salt",
                    "-in",
                    str(backup_file),
                    "-out",
                    str(encrypted_file),
                    "-pass",
                    f"file:{passphrase_path}",
                ],
                check=True,
            )
        except (ShellCommandError, FileNotFoundError) as exc:
            raise DbOffsiteError(str(exc)) from exc

    manifest_file = encrypted_manifest_path(encrypted_file)
    source_manifest_path = backup_file.with_name(f"{backup_file.name}.json")
    source_manifest: dict[str, Any] | None = None
    if source_manifest_path.exists():
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))

    payload = {
        "encrypted_file": encrypted_file.name,
        "size_bytes": encrypted_file.stat().st_size,
        "sha256": _sha256_file(encrypted_file),
        "created_at": datetime.now(UTC).isoformat(),
        "cipher": encryption_config.cipher,
        "source": {
            "backup_file": str(backup_file),
            "backup_name": backup_file.name,
            "size_bytes": backup_file.stat().st_size,
            "sha256": _sha256_file(backup_file),
            "manifest": source_manifest,
        },
    }
    manifest_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return encrypted_file, manifest_file


def decrypt_backup_artifact(*, encrypted_file: Path, output_file: Path, encryption_config: BackupEncryptionConfig) -> Path:
    _validate_encryption_config(encryption_config)
    if not encrypted_file.exists():
        raise DbOffsiteError(f"Encrypted backup file not found: {encrypted_file}")
    with _passphrase_file(encryption_config.passphrase) as passphrase_path:
        try:
            run_command(
                [
                    "openssl",
                    "enc",
                    f"-{encryption_config.cipher}",
                    "-d",
                    "-pbkdf2",
                    "-in",
                    str(encrypted_file),
                    "-out",
                    str(output_file),
                    "-pass",
                    f"file:{passphrase_path}",
                ],
                check=True,
            )
        except (ShellCommandError, FileNotFoundError) as exc:
            raise DbOffsiteError(str(exc)) from exc
    return output_file


def upload_offsite_backup(
    *,
    domain: str,
    backup_file: Path,
    encryption_config: BackupEncryptionConfig,
    offsite_config: BackupOffsiteConfig,
) -> dict[str, Any]:
    client = _client(offsite_config)
    encrypted_file, manifest_file = encrypt_backup_artifact(backup_file=backup_file, encryption_config=encryption_config)
    prefix = offsite_object_prefix(offsite_config=offsite_config, domain=domain)
    object_key = f"{prefix}/{encrypted_file.name}"
    manifest_key = f"{prefix}/{manifest_file.name}"
    extra_args = {"StorageClass": offsite_config.storage_class}
    try:
        client.upload_file(str(encrypted_file), offsite_config.bucket, object_key, ExtraArgs=extra_args)
        client.upload_file(str(manifest_file), offsite_config.bucket, manifest_key, ExtraArgs=extra_args)
    except Exception as exc:  # noqa: BLE001
        raise DbOffsiteError(str(exc)) from exc

    try:
        deleted = prune_offsite_by_age(domain=domain, offsite_config=offsite_config)
        return {
            "bucket": offsite_config.bucket,
            "provider": offsite_config.provider,
            "object_key": object_key,
            "manifest_key": manifest_key,
            "encrypted_file": encrypted_file.name,
            "manifest_file": manifest_file.name,
            "deleted": deleted,
        }
    finally:
        encrypted_file.unlink(missing_ok=True)
        manifest_file.unlink(missing_ok=True)


def prune_offsite_by_age(*, domain: str, offsite_config: BackupOffsiteConfig) -> list[str]:
    client = _client(offsite_config)
    prefix = offsite_object_prefix(offsite_config=offsite_config, domain=domain)
    threshold = datetime.now(UTC) - timedelta(days=offsite_config.retention_days)
    paginator = client.get_paginator("list_objects_v2")
    deleted: list[str] = []
    try:
        for page in paginator.paginate(Bucket=offsite_config.bucket, Prefix=f"{prefix}/"):
            for item in page.get("Contents", []):
                last_modified = item["LastModified"].astimezone(UTC)
                if last_modified < threshold:
                    client.delete_object(Bucket=offsite_config.bucket, Key=item["Key"])
                    deleted.append(str(item["Key"]))
    except Exception as exc:  # noqa: BLE001
        raise DbOffsiteError(str(exc)) from exc
    return deleted


def offsite_status(*, domain: str, offsite_config: BackupOffsiteConfig, stale_hours: int) -> dict[str, Any]:
    client = _client(offsite_config)
    prefix = offsite_object_prefix(offsite_config=offsite_config, domain=domain)
    paginator = client.get_paginator("list_objects_v2")
    backups: list[dict[str, Any]] = []
    try:
        for page in paginator.paginate(Bucket=offsite_config.bucket, Prefix=f"{prefix}/"):
            for item in page.get("Contents", []):
                key = str(item["Key"])
                if key.endswith(".enc"):
                    backups.append(item)
    except Exception as exc:  # noqa: BLE001
        raise DbOffsiteError(str(exc)) from exc

    if not backups:
        return {
            "status": "warn",
            "bucket": offsite_config.bucket,
            "prefix": prefix,
            "count": 0,
            "latest_object": None,
            "age_hours": None,
        }

    latest = max(backups, key=lambda item: item["LastModified"])
    age_hours = (datetime.now(UTC) - latest["LastModified"].astimezone(UTC)).total_seconds() / 3600
    status = "ok" if age_hours <= stale_hours else "warn"
    return {
        "status": status,
        "bucket": offsite_config.bucket,
        "prefix": prefix,
        "count": len(backups),
        "latest_object": str(latest["Key"]),
        "age_hours": round(age_hours, 2),
    }


@contextmanager
def _download_artifact(*, domain: str, offsite_config: BackupOffsiteConfig, object_key: str | None) -> Iterator[dict[str, Any]]:
    client = _client(offsite_config)
    prefix = offsite_object_prefix(offsite_config=offsite_config, domain=domain)
    target_object_key = object_key
    if target_object_key is None:
        paginator = client.get_paginator("list_objects_v2")
        backups: list[dict[str, Any]] = []
        try:
            for page in paginator.paginate(Bucket=offsite_config.bucket, Prefix=f"{prefix}/"):
                for item in page.get("Contents", []):
                    if str(item["Key"]).endswith(".enc"):
                        backups.append(item)
        except Exception as exc:  # noqa: BLE001
            raise DbOffsiteError(str(exc)) from exc
        if not backups:
            raise DbOffsiteError(f"No offsite backups found for {domain}.")
        target_object_key = str(max(backups, key=lambda item: item["LastModified"])["Key"])

    manifest_key = f"{target_object_key}.json"
    with _temporary_dir(prefix="larops-offsite-download-") as tmp_dir:
        encrypted_file = tmp_dir / Path(target_object_key).name
        manifest_file = tmp_dir / Path(manifest_key).name
        try:
            client.download_file(offsite_config.bucket, target_object_key, str(encrypted_file))
            client.download_file(offsite_config.bucket, manifest_key, str(manifest_file))
        except Exception as exc:  # noqa: BLE001
            raise DbOffsiteError(str(exc)) from exc
        yield {
            "object_key": target_object_key,
            "manifest_key": manifest_key,
            "encrypted_file": encrypted_file,
            "manifest_file": manifest_file,
        }


def offsite_restore_verify(
    *,
    domain: str,
    database: str,
    credential_file: Path,
    engine: str,
    encryption_config: BackupEncryptionConfig,
    offsite_config: BackupOffsiteConfig,
    object_key: str | None = None,
    verify_database: str | None = None,
) -> dict[str, Any]:
    with _download_artifact(domain=domain, offsite_config=offsite_config, object_key=object_key) as artifact:
        try:
            manifest = json.loads(artifact["manifest_file"].read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DbOffsiteError(f"Invalid offsite manifest: {artifact['manifest_file']}") from exc

        actual_encrypted_sha = _sha256_file(artifact["encrypted_file"])
        expected_encrypted_sha = str(manifest.get("sha256", ""))
        if expected_encrypted_sha and actual_encrypted_sha != expected_encrypted_sha:
            raise DbOffsiteError("Offsite encrypted backup sha256 mismatch.")

        decrypted_backup = artifact["encrypted_file"].with_suffix("")
        decrypt_backup_artifact(
            encrypted_file=artifact["encrypted_file"],
            output_file=decrypted_backup,
            encryption_config=encryption_config,
        )
        source_manifest = manifest.get("source", {}).get("manifest")
        if source_manifest:
            source_manifest_path = Path(f"{decrypted_backup}.json")
            source_manifest_path.write_text(json.dumps(source_manifest, indent=2), encoding="utf-8")
        verify_payload = verify_backup(backup_file=decrypted_backup, require_manifest=False)
        result = restore_verify_backup(
            backup_file=decrypted_backup,
            database=database,
            credential_file=credential_file,
            engine=engine,
            verify_database=verify_database,
        )
        result["offsite_object_key"] = artifact["object_key"]
        result["offsite_manifest_key"] = artifact["manifest_key"]
        result["download_verification"] = verify_payload
        return result
