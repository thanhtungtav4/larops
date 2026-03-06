from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from larops.config import BackupEncryptionConfig, BackupOffsiteConfig
from larops.services.db_offsite_service import (
    DbOffsiteError,
    encrypt_backup_artifact,
    offsite_restore_verify,
    offsite_status,
    upload_offsite_backup,
)


def encryption_config() -> BackupEncryptionConfig:
    return BackupEncryptionConfig(enabled=True, passphrase="secret-passphrase", cipher="aes-256-cbc")


def offsite_config() -> BackupOffsiteConfig:
    return BackupOffsiteConfig(
        enabled=True,
        provider="s3",
        bucket="larops-backups",
        prefix="prod/backups",
        region="auto",
        endpoint_url="https://example.invalid",
        access_key_id="key-id",
        secret_access_key="secret-key",
        retention_days=14,
        stale_hours=12,
    )


def test_encrypt_backup_artifact_includes_hmac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup_file = tmp_path / "backup.sql.gz"
    backup_file.write_bytes(b"backup-data")

    def fake_run_command(command: list[str], *, check: bool = True, timeout_seconds: int | None = None) -> CompletedProcess[str]:
        input_path = Path(command[command.index("-in") + 1])
        output_path = Path(command[command.index("-out") + 1])
        output_path.write_bytes(input_path.read_bytes())
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("larops.services.db_offsite_service.run_command", fake_run_command)

    encrypted_file, manifest_file = encrypt_backup_artifact(
        backup_file=backup_file,
        encryption_config=encryption_config(),
    )

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["hmac_sha256"]
    assert manifest["sha256"]
    assert manifest["hmac_sha256"] != manifest["sha256"]
    encrypted_file.unlink(missing_ok=True)
    manifest_file.unlink(missing_ok=True)


def test_offsite_status_errors_when_latest_backup_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)

    class FakePaginator:
        def paginate(self, **_: object):
            yield {
                "Contents": [
                    {"Key": "prod/backups/demo.test/demo_old.sql.gz.enc", "LastModified": now - timedelta(hours=4)},
                    {"Key": "prod/backups/demo.test/demo_old.sql.gz.enc.json", "LastModified": now - timedelta(hours=4)},
                    {"Key": "prod/backups/demo.test/demo_new.sql.gz.enc", "LastModified": now - timedelta(minutes=5)},
                ]
            }

    class FakeClient:
        def get_paginator(self, name: str) -> FakePaginator:
            assert name == "list_objects_v2"
            return FakePaginator()

    monkeypatch.setattr("larops.services.db_offsite_service._client", lambda _: FakeClient())

    status = offsite_status(domain="demo.test", offsite_config=offsite_config(), stale_hours=24)

    assert status["status"] == "error"
    assert status["latest_object"] == "prod/backups/demo.test/demo_old.sql.gz.enc"
    assert status["incomplete_objects"] == ["prod/backups/demo.test/demo_new.sql.gz.enc"]


def test_upload_offsite_backup_deletes_partial_remote_on_manifest_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_file = tmp_path / "backup.sql.gz"
    backup_file.write_bytes(b"backup-data")
    encrypted_file = tmp_path / "backup.sql.gz.enc"
    encrypted_file.write_bytes(b"encrypted")
    manifest_file = tmp_path / "backup.sql.gz.enc.json"
    manifest_file.write_text("{}", encoding="utf-8")
    deleted: list[str] = []

    class FakeClient:
        def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict[str, str]) -> None:
            if key.endswith(".json"):
                raise RuntimeError("manifest upload failed")

        def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
            deleted.append(Key)

    monkeypatch.setattr("larops.services.db_offsite_service._client", lambda _: FakeClient())
    monkeypatch.setattr(
        "larops.services.db_offsite_service.encrypt_backup_artifact",
        lambda **_: (encrypted_file, manifest_file),
    )

    with pytest.raises(DbOffsiteError):
        upload_offsite_backup(
            domain="demo.test",
            backup_file=backup_file,
            encryption_config=encryption_config(),
            offsite_config=offsite_config(),
        )

    assert deleted == ["prod/backups/demo.test/backup.sql.gz.enc"]


def test_offsite_restore_verify_rejects_hmac_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    encrypted_file = tmp_path / "backup.sql.gz.enc"
    encrypted_file.write_bytes(b"encrypted")
    manifest_file = tmp_path / "backup.sql.gz.enc.json"
    manifest_file.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(b"encrypted").hexdigest(),
                "hmac_sha256": "invalid",
            }
        ),
        encoding="utf-8",
    )

    @contextmanager
    def fake_download_artifact(**_: object):
        yield {
            "object_key": "prod/backups/demo.test/backup.sql.gz.enc",
            "manifest_key": "prod/backups/demo.test/backup.sql.gz.enc.json",
            "encrypted_file": encrypted_file,
            "manifest_file": manifest_file,
        }

    monkeypatch.setattr("larops.services.db_offsite_service._download_artifact", fake_download_artifact)

    with pytest.raises(DbOffsiteError, match="HMAC mismatch"):
        offsite_restore_verify(
            domain="demo.test",
            database="appdb",
            credential_file=tmp_path / "db.cnf",
            engine="mysql",
            encryption_config=encryption_config(),
            offsite_config=offsite_config(),
        )
