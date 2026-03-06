# LarOps

Laravel-first server operations CLI for fast provisioning, deployment, runtime control, and safe lifecycle management on Linux servers.

## Table of Contents

1. [Why LarOps](#why-larops)
2. [Feature Overview](#feature-overview)
3. [Requirements](#requirements)
4. [Quick Start](#quick-start)
5. [Core Concepts](#core-concepts)
6. [Configuration](#configuration)
7. [Standard Operations Flow](#standard-operations-flow)
8. [Site Profile Presets](#site-profile-presets)
9. [Runtime Policy Matrix](#runtime-policy-matrix)
10. [Command Cheat Sheet](#command-cheat-sheet)
11. [Telegram Notifications](#telegram-notifications)
12. [Docker and Local QA](#docker-and-local-qa)
13. [GitHub CI/CD](#github-cicd)
14. [Release Process](#release-process)
15. [Security and Safety Notes](#security-and-safety-notes)
16. [Troubleshooting](#troubleshooting)
17. [Repository Structure](#repository-structure)
18. [Production Runbook](#production-runbook)

## Why LarOps

LarOps is designed for teams that want WordOps-like speed, but focused on Laravel operations.

Main goals:

- Bootstrap an empty server quickly.
- Create and deploy Laravel sites with one command.
- Manage runtime processes (`worker`, `scheduler`, `horizon`) with consistent safety controls.
- Enforce safer destructive actions (`--purge` + guard + checkpoint).
- Keep release workflow predictable with CI/CD and SemVer tags.

## Feature Overview

- Stack provisioning (`stack install`, `bootstrap init`).
- App lifecycle (`app create`, `app deploy`, `app rollback`, `app info`).
- Site lifecycle shortcuts (`site create`, `site runtime`, `site permissions`, `site delete`).
- SSL lifecycle (`ssl issue`, `ssl renew`, `ssl check`).
- Database backup/restore and credentials (`db backup`, `db restore`, `db credential`).
- Event-stream based notifications (`notify telegram run-once/watch/daemon`).
- Security baseline controls (`security install/status/report/posture`, `alert set/test`).
- Preventive hardening controls (`secure ssh`, `secure nginx`).
- Monitor controls (`monitor scan run`, `monitor fim init/run`, `monitor service run`, `monitor app run`).
- Health checks (`doctor run`, `doctor quick`).
- Fleet-wide health summary (`doctor fleet`).
- Prometheus textfile export (`doctor metrics run`).
- Vector-based log shipping hook (`observability logs enable/status/disable`).
- Runtime protection with restart policy matrix and auto-heal.

## Requirements

Production host requirements:

- Linux server with `systemd` (recommended for production runtime control).
- `python >= 3.11`.
- Root or sudo access for stack install and system-level operations.
- Network access to install packages and call external APIs (for SSL/Telegram).

Development requirements:

- Python 3.11+.
- Docker (optional, for containerized test flow).

## Quick Start

### Empty server one-liner

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
larops bootstrap init --apply
larops create site example.com --apply
```

Install pinned version:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

Allow unpinned latest/main explicitly (not recommended for production):

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=latest LAROPS_ALLOW_UNPINNED=true bash
```

### Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
larops --help
pytest -q
```

## Core Concepts

### Plan mode first, apply second

Most commands are safe by default:

- Without `--apply`: preview only (plan mode).
- With `--apply`: execute changes.

This pattern is used across site/runtime/db/notify commands.

### State model

LarOps stores state metadata under `state_path`:

- App metadata (`state/apps/<domain>.json`)
- Runtime specs (`state/runtime/<domain>/*.json`)
- DB credentials (`state/secrets/db/<domain>.cnf`)
- Event stream file (`events.path`)

### Runtime behavior

Runtime processes are represented by JSON specs and optional systemd units:

- `worker`
- `scheduler`
- `horizon`

When `systemd.manage=true`, LarOps manages unit lifecycle (enable/disable/status/restart).

## Configuration

Default config path:

- `/etc/larops/larops.yaml`

You can override with:

```bash
larops --config /path/to/larops.yaml ...
```

Example config:

```yaml
environment: production
state_path: /var/lib/larops/state

deploy:
  releases_path: /var/www
  source_base_path: /var/www/source
  keep_releases: 5
  build_timeout_seconds: 1800
  pre_activate_timeout_seconds: 900
  post_activate_timeout_seconds: 900
  health_check_path: /up
  health_check_enabled: false
  health_check_scheme: http
  health_check_host: 127.0.0.1
  health_check_timeout_seconds: 5
  health_check_retries: 3
  health_check_retry_delay_seconds: 1
  health_check_expected_status: 200
  health_check_use_domain_host_header: true
  rollback_on_health_check_failure: false
  runtime_refresh_strategy: none
  shared_dirs:
    - storage
    - bootstrap/cache
  shared_files:
    - .env
  composer_install: false
  composer_binary: composer
  composer_no_dev: true
  composer_optimize_autoloader: true
  asset_commands: []
  migrate_enabled: false
  migrate_phase: post-activate
  migrate_command: php artisan migrate --force
  cache_warm_enabled: false
  cache_warm_commands:
    - php artisan config:cache
    - php artisan route:cache
    - php artisan view:cache
    - php artisan event:cache
  verify_timeout_seconds: 300
  verify_commands: []
  rollback_on_verify_failure: false
  pre_activate_commands: []
  post_activate_commands: []

systemd:
  manage: true
  unit_dir: /etc/systemd/system
  user: www-data

runtime_policy:
  worker:
    max_restarts: 5
    window_seconds: 300
    cooldown_seconds: 120
    auto_heal: true
  scheduler:
    max_restarts: 3
    window_seconds: 300
    cooldown_seconds: 120
    auto_heal: true
  horizon:
    max_restarts: 3
    window_seconds: 300
    cooldown_seconds: 120
    auto_heal: true

events:
  sink: jsonl
  path: /var/log/larops/events.jsonl

notifications:
  telegram:
    enabled: false
    bot_token: ""
    bot_token_file: /etc/larops/secrets/telegram_bot_token
    chat_id: ""
    chat_id_file: /etc/larops/secrets/telegram_chat_id
    min_severity: error
    batch_size: 20

backups:
  encryption:
    enabled: false
    passphrase: ""
    passphrase_file: /etc/larops/secrets/backup_passphrase
    cipher: aes-256-cbc
  offsite:
    enabled: false
    provider: s3
    bucket: ""
    prefix: larops/backups
    region: us-east-1
    endpoint_url: ""
    access_key_id: ""
    access_key_id_file: /etc/larops/secrets/offsite_access_key_id
    secret_access_key: ""
    secret_access_key_file: /etc/larops/secrets/offsite_secret_access_key
    storage_class: STANDARD
    retention_days: 30
    stale_hours: 24

doctor:
  app_command_checks: []
  heartbeat_checks: []
  queue_backlog_checks: []
  failed_job_checks: []
```

Environment overrides are supported for key fields (including Telegram settings).

Important:

- Secret file overrides are fail-fast (missing/empty file causes config error).
- Invalid numeric env values (for example invalid batch size) also fail fast.

## Standard Operations Flow

### 1. Bootstrap host

```bash
larops bootstrap init --apply
```

Optional one-shot bootstrap + first site deploy:

```bash
larops bootstrap init --domain example.com --source /var/www/source/example.com --apply
```

### 2. Create/deploy site

Two equivalent entry points:

```bash
larops create site example.com --apply
larops site create example.com --apply
```

Default source behavior:

- If `--source` is omitted, LarOps deploys from `deploy.source_base_path/<domain>`.

With profile preset and cache preset:

```bash
larops site create example.com --type laravel --cache redis --php 8.3 --apply
```

With Let's Encrypt:

```bash
larops site create example.com -le --le-email ops@example.com --apply
```

With transactional guard:

```bash
larops site create example.com -le --atomic --apply
```

### 3. Manage runtime

```bash
larops site runtime enable example.com -w -s -a
larops site runtime status example.com
larops site runtime disable example.com -a
```

Direct process commands remain available:

```bash
larops worker enable example.com --queue default --concurrency 2 --apply
larops scheduler enable example.com --apply
larops horizon enable example.com --apply
```

### 4. Re-assign permissions

```bash
larops site permissions example.com --apply
larops site permissions example.com --owner www-data --group www-data --apply
```

Custom modes and writable paths:

```bash
larops site permissions example.com \
  --dir-mode 755 \
  --file-mode 644 \
  --writable-mode 775 \
  --writable shared/storage \
  --writable current/storage \
  --apply
```

### 5. SSL lifecycle

```bash
larops ssl issue example.com --challenge http --apply
larops ssl auto-renew enable --apply
larops ssl auto-renew status
larops ssl renew --apply
larops ssl check example.com
```

Auto-renew notes:

- Uses systemd timer/service units: `larops-ssl-renew.timer` and `larops-ssl-renew.service`
- Default schedule: `*-*-* 03,15:00:00`
- Default deploy hook: `systemctl reload nginx`
- Customize schedule:

```bash
larops ssl auto-renew enable --on-calendar "*-*-* 01:30:00" --randomized-delay 900 --apply
```

Disable and remove unit files:

```bash
larops ssl auto-renew disable --remove-units --apply
```

### 6. DB backup/restore

```bash
export LAROPS_DB_PASSWORD="strong-password"
larops db credential set example.com --user appuser --apply
larops db backup example.com --database appdb --retain-count 10 --apply
larops db status example.com
larops db verify --backup-file /path/backup.sql.gz
larops db restore-verify example.com --backup-file /path/backup.sql.gz --database appdb --apply
larops db offsite status example.com
larops db offsite restore-verify example.com --database appdb --apply
larops db auto-backup enable example.com --database appdb --apply
larops db auto-backup status example.com
larops db list-backups example.com
larops db restore example.com --backup-file /path/backup.sql.gz --database appdb --apply
```

PostgreSQL example:

```bash
export LAROPS_DB_PASSWORD="strong-password"
larops db credential set example.com --engine postgres --user appuser --apply
larops db backup example.com --engine postgres --database appdb --retain-count 10 --apply
larops db restore-verify example.com --engine postgres --backup-file /path/backup.sql.gz --database appdb --apply
larops db offsite restore-verify example.com --engine postgres --database appdb --apply
larops db restore example.com --engine postgres --backup-file /path/backup.sql.gz --database appdb --apply
```

Offsite backup notes:

- `db backup` will automatically encrypt and upload to configured S3-compatible storage when `backups.offsite.enabled=true` and `backups.encryption.enabled=true`.
- Supported current backend: `s3` (works for AWS S3, Cloudflare R2, MinIO, and compatible endpoints).
- Encryption is client-side via `openssl` before upload, and LarOps now stores an HMAC alongside the encrypted artifact to detect remote tampering during restore verification.
- Remote retention is pruned by age using `backups.offsite.retention_days`.
- `db offsite status` and `doctor run` will flag incomplete remote uploads when an encrypted object exists without its manifest pair.

### 7. Health checks

```bash
larops doctor quick
larops --json doctor run example.com
larops --json doctor fleet
larops --json doctor fleet --quick --include-checks
larops doctor metrics run
larops doctor metrics run --output-file /var/lib/node_exporter/textfile_collector/larops.prom --apply
larops doctor metrics timer enable --output-file /var/lib/node_exporter/textfile_collector/larops.prom --apply
larops observability logs enable --sink vector --vector-address 10.0.0.10:6000 --apply
larops observability logs enable --sink http --http-uri https://logs.example.com/ingest --http-env-file /etc/larops/vector-http.env --apply
larops observability logs status
larops observability logs disable --remove-files --apply
```

Notes:

- `observability logs enable --sink=http` now requires `--http-env-file` and a non-empty bearer token variable inside that file.
- `observability logs disable --remove-files` only removes `data_dir` paths under `state_path/observability` and only when LarOps marker metadata exists.

Optional app-level probes via `doctor.app_command_checks`, `doctor.heartbeat_checks`, `doctor.queue_backlog_checks`, and `doctor.failed_job_checks`:

```yaml
doctor:
  heartbeat_checks:
    - name: scheduler-heartbeat
      path: storage/app/larops/scheduler-heartbeat
      max_age_seconds: 180
  queue_backlog_checks:
    - name: default-queue
      connection: redis
      queue: default
      max_size: 100
      timeout_seconds: 30
  failed_job_checks:
    - name: failed-jobs
      max_count: 0
      timeout_seconds: 30
  app_command_checks:
    - name: app-about
      command: "php artisan about --only=environment"
      timeout_seconds: 30
```

Turn these probes into stateful alerts:

```bash
larops monitor app run example.com --apply
larops monitor app timer enable example.com --on-calendar "*-*-* *:*:00" --apply
```

### 8. Safe delete (guarded)

```bash
larops site delete example.com --purge --confirm example.com --apply
larops site restore example.com --checkpoint-file /path/checkpoint.tar.gz --apply
```

Or non-interactive guard bypass:

```bash
larops site delete example.com --purge --no-prompt --apply
```

Delete behavior:

- Requires `--purge`.
- Requires `--confirm <domain>` unless `--no-prompt`.
- Creates checkpoint archive by default before purge.
- Checkpoint excludes secret files by default; use `--checkpoint-include-secrets` only when needed.

## Site Profile Presets

`site create` supports preset mapping with override capability.

Type presets:

- `--type php`: runtime off, `db=none`, `ssl=false`
- `--type mysql`: runtime off, `db=mysql`, `ssl=false`
- `--type laravel`: worker+scheduler on, `db=mysql`, `ssl=true`
- `--type queue`: worker+scheduler on, `db=mysql`, `ssl=true`
- `--type horizon`: scheduler+horizon on, `db=mysql`, `ssl=true`

Cache presets:

- `--cache none`
- `--cache fastcgi`: sets `ssl=true`
- `--cache redis`: sets `worker=true`, `ssl=true`
- `--cache supercache`: sets `ssl=true`

Override priority:

- Manual flags override presets (`--no-worker`, `--db`, `--ssl`, `--php`, etc.).

## Runtime Policy Matrix

Each process (`worker`, `scheduler`, `horizon`) supports:

- `max_restarts`: max restart attempts inside rolling window.
- `window_seconds`: rolling window length.
- `cooldown_seconds`: block further restart attempts after threshold.
- `auto_heal`: status surface reports whether runtime is healthy enough for reconcile policy.

Behavior:

- Manual restart respects rate limit and cooldown.
- Manual reconcile uses the same rate limits before attempting restart.
- Policy is written into runtime spec for traceability.

## Command Cheat Sheet

Stack and bootstrap:

```bash
larops stack install --web --data --ops --apply
larops bootstrap init --apply
```

App lifecycle:

```bash
larops app create example.com --apply
larops app deploy example.com --source /var/www/source/example.com --apply
larops app rollback example.com --to previous --apply
larops --json app info example.com
```

Site lifecycle:

```bash
larops site create example.com --apply
larops site runtime enable example.com -w -s -a
larops site runtime reconcile example.com -w -a
larops site permissions example.com --apply
larops site delete example.com --purge --confirm example.com --apply
```

Runtime direct:

```bash
larops worker status example.com
larops scheduler run-once example.com --apply
larops horizon terminate example.com --apply
```

SSL:

```bash
larops ssl auto-renew enable --apply
larops ssl auto-renew disable --apply
larops ssl auto-renew status
```

Database:

```bash
larops db credential show example.com
larops db backup example.com --database appdb --apply
larops db backup example.com --engine postgres --database appdb --apply
larops db status example.com
larops db offsite status example.com
larops db verify --backup-file /path/backup.sql.gz
larops db offsite restore-verify example.com --database appdb --apply
larops db auto-backup enable example.com --database appdb --apply
larops db auto-backup status example.com
larops db list-backups example.com
```

Notifications:

```bash
larops notify telegram run-once --apply
larops notify telegram daemon enable --apply
larops notify telegram daemon status
larops alert set --telegram-token "<token>" --telegram-chat-id "<chat-id>" --apply
larops alert test --apply
```

Security baseline:

```bash
larops security install --apply
larops security status
larops security posture
larops security report
larops security report --since 24h
larops secure ssh --ssh-key-only --apply
larops secure ssh --ssh-key-only --allow-user deploy --allow-group wheel --max-startups 10:30:60 --apply
larops secure nginx --server-config-file /etc/nginx/sites-enabled/example.conf --apply
larops secure nginx --profile strict --block-path /private/ --server-config-file /etc/nginx/sites-enabled/example.conf --apply
```

Monitor:

```bash
larops monitor scan run --apply
larops monitor scan run --threshold-hits 8 --window-seconds 300 --apply
larops monitor fim init --root /var/www/example.com/current --apply
larops monitor fim run --apply
larops monitor service run --service mariadb --service redis --apply
larops monitor service run --profile laravel-host --apply
larops monitor app run example.com --apply
larops monitor scan timer enable --on-calendar "*-*-* *:*:00" --apply
larops monitor fim timer enable --on-calendar "*-*-* *:15:00" --apply
larops monitor service timer enable --service mariadb --service redis --on-calendar "*-*-* *:*:00" --apply
larops monitor service timer enable --profile laravel-host --on-calendar "*-*-* *:*:00" --apply
larops monitor app timer enable example.com --on-calendar "*-*-* *:*:00" --apply
```

Default profile suggestions:

- Small VPS: scan `*-*-* *:0/2:00`, fim `*-*-* *:0/30:00`, service watchdog `*-*-* *:*:00`
- High traffic: scan `*-*-* *:*:00`, fim `*-*-* *:0/10:00`, service watchdog `*-*-* *:*:00`

`monitor scan` now evaluates `threshold-hits` inside a rolling `window-seconds` window instead of only per batch read. Keep `window-seconds` aligned with your timer cadence and attack tolerance.

Service watchdog emits `monitor.service.*` events into the existing JSONL event stream. If `notify telegram daemon` is enabled, LarOps will send Telegram alerts when a watched service goes down, is restarted, fails to restart, or recovers.
Built-in profiles: `laravel-host` (`nginx`, `php-fpm`, `mariadb`, `redis`) and `laravel-postgres-host` (`nginx`, `php-fpm`, `postgresql`, `redis`).

## Telegram Notifications

Create secret files:

```bash
sudo install -d -m 700 /etc/larops/secrets
echo "123456:BOT_TOKEN" | sudo tee /etc/larops/secrets/telegram_bot_token >/dev/null
echo "-1001234567890" | sudo tee /etc/larops/secrets/telegram_chat_id >/dev/null
sudo chmod 600 /etc/larops/secrets/telegram_bot_token /etc/larops/secrets/telegram_chat_id
```

Optional daemon env file:

```bash
sudo tee /etc/larops/telegram.env >/dev/null <<'ENV'
LAROPS_TELEGRAM_ENABLED=true
LAROPS_TELEGRAM_BOT_TOKEN_FILE=/etc/larops/secrets/telegram_bot_token
LAROPS_TELEGRAM_CHAT_ID_FILE=/etc/larops/secrets/telegram_chat_id
ENV
sudo chmod 600 /etc/larops/telegram.env
```

## Docker and Local QA

```bash
docker compose build
docker compose run --rm larops-test
docker compose run --rm larops-cli
```

Run DB integration tests (requires reachable MySQL/PostgreSQL instances):

```bash
LAROPS_RUN_DB_INTEGRATION=1 \
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD=rootpass \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgrespass \
pytest -q tests/integration/test_db_engine_integration.py
```

## GitHub CI/CD

Workflow files:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

CI (`ci.yml`) triggers:

- Pull requests to `main`
- Pushes to `main`
- Manual dispatch

CI stages:

- Ruff + Pytest on Python `3.11`, `3.12`, `3.13`
- DB integration tests with real `mysql:8` and `postgres:16` services
- Docker build + in-container tests + CLI smoke

Release (`release.yml`) trigger:

- Push tag matching `vX.Y.Z` (example `v0.2.0`)

Release stages:

- Validate tag format and exact version match in:
  - `pyproject.toml`
  - `src/larops/__init__.py`
- Run Ruff + Pytest
- Build artifacts (`sdist` + `wheel`)
- Build release assets:
  - `dist/*`
  - `larops-vX.Y.Z.tar.gz` source archive
  - `SHA256SUMS`
- Generate build provenance attestation for release assets
- Create GitHub Release and upload:
  - `dist/*`
  - `SHA256SUMS`
  - `larops-vX.Y.Z.tar.gz`
  - `scripts/install.sh`

Repository setting required for release job:

- `Settings -> Actions -> Workflow permissions -> Read and write permissions`

## Release Process

Use the included release script:

```bash
scripts/release.sh <version>
```

Script responsibilities:

- Bump version in `pyproject.toml` and `src/larops/__init__.py`
- Create changelog section in `CHANGELOG.md`
- Create release commit and annotated tag (`v<version>`)

Then push:

```bash
git push origin main
git push origin v<version>
```

After tag push, GitHub release pipeline runs automatically.

## Security and Safety Notes

- Treat all destructive operations as two-step (`plan` then `--apply`).
- `site delete` requires explicit safety guards.
- Prefer secret files over plain env for credentials/tokens.
- Keep secret files permissioned as owner-only (`0600`).
- Use `--atomic` on `site create` for rollback on failure path.
- Prefer pinned installer (`LAROPS_VERSION=x.y.z`) with checksum verification.

## Troubleshooting

### "Application is not registered"

Root cause:

- Domain metadata missing in `state/apps/<domain>.json`.

Fix:

- Create site/app first:
  - `larops site create <domain> --apply`

### Runtime enable fails with "Deploy app before enabling ..."

Root cause:

- `current` release symlink missing.

Fix:

- Deploy first:
  - `larops app deploy <domain> --source <path> --apply`

### Config error from Telegram secret file

Root cause:

- `bot_token_file` or `chat_id_file` path missing/empty.

Fix:

- Create file, add value, set permission `600`, retry.

### Release workflow fails on version mismatch

Root cause:

- Tag version does not match project files.

Fix:

- Use `scripts/release.sh <semver>` to keep versions aligned.

### SSL auto-renew timer exists but not active

Root cause:

- `systemd.manage` is disabled, or timer was not enabled after unit change.

Fix:

- Verify config and timer state:
  - `larops ssl auto-renew status`
- Re-enable timer:
  - `larops ssl auto-renew enable --apply`

### DB backup/restore fails due credential permission

Root cause:

- Credential file mode is not owner-only (`0600`).

Fix:

- Repair permission and retry:
  - `chmod 600 /var/lib/larops/state/secrets/db/<domain>.cnf`
  - `larops db backup <domain> --database <db> --apply`

## Repository Structure

```text
src/larops/
  commands/        CLI command groups
  services/        business logic and integrations
  core/            shell, locks, event primitives
  config.py        config models and env overrides

scripts/
  install.sh       install bootstrap script
  release.sh       semver release helper

.github/workflows/
  ci.yml           PR/push CI pipeline
  release.yml      tag-based release pipeline

tests/
  CLI and service tests
```

## Production Runbook

Detailed production checklist and procedures:

- [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)
