# LarOps

Laravel-first server operations CLI for fast provisioning, deployment, runtime control, and safe lifecycle management on Linux servers.

Language:

- Landing page: [README.md](README.md)
- Vietnamese manual: [README.vi.md](README.vi.md)
- Command index: [docs/COMMANDS.md](docs/COMMANDS.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Table of Contents

1. [Why LarOps](#why-larops)
2. [Feature Overview](#feature-overview)
3. [Requirements](#requirements)
4. [Quick Start](#quick-start)
5. [Core Concepts](#core-concepts)
6. [What High-Level Commands Actually Do](#what-high-level-commands-actually-do)
7. [Configuration](#configuration)
8. [Standard Operations Flow](#standard-operations-flow)
9. [Site Profile Presets](#site-profile-presets)
10. [Runtime Policy Matrix](#runtime-policy-matrix)
11. [Command Cheat Sheet](#command-cheat-sheet)
12. [Telegram Notifications](#telegram-notifications)
13. [Docker and Local QA](#docker-and-local-qa)
14. [GitHub CI/CD](#github-cicd)
15. [Release Process](#release-process)
16. [Security and Safety Notes](#security-and-safety-notes)
17. [Troubleshooting](#troubleshooting)
18. [Repository Structure](#repository-structure)
19. [Production Runbook](#production-runbook)
20. [OS Support Matrix](#os-support-matrix)

## Why LarOps

LarOps is designed for teams that want WordOps-like speed, but focused on Laravel operations.

Main goals:

- Bootstrap an empty server quickly.
- Create and deploy Laravel sites with one command.
- Manage runtime processes (`worker`, `scheduler`, `horizon`) with consistent safety controls.
- Enforce safer destructive actions (`--purge` + guard + checkpoint).
- Keep release workflow predictable with CI/CD and SemVer tags.

Current best fit:

- Serious single-node Laravel operations on Linux hosts with `systemd`.

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

Production-supported operating systems:

- Ubuntu 24.04 LTS
- Ubuntu 22.04 LTS
- Debian 12

Preview / evaluation targets:

- Debian 13
- Rocky Linux 9
- AlmaLinux 9
- RHEL 9

Detailed support status:

- [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)

Practical scope:

- If you want the lowest-risk production path, stay on Ubuntu 22.04/24.04 or Debian 12.
- Debian 13 and EL9-family hosts are useful for validation and early rollout work, but they are not the primary production support scope yet.

Practical VPS sizing guidance:

- Minimum lab / evaluation:
  - 1 vCPU
  - 1 GB RAM
  - 20 GB SSD
  - suitable only for CLI evaluation, not serious Laravel production
- Minimum small production:
  - 2 vCPU
  - 2 GB RAM
  - 40 GB SSD
  - suitable for one small Laravel app with low traffic, careful queue usage, and limited background jobs
- Recommended serious single-node Laravel host:
  - 4 vCPU
  - 4 to 8 GB RAM
  - 80+ GB SSD
  - suitable for Nginx + PHP-FPM + MariaDB/Postgres + Redis + workers + monitoring on one box
- Heavier queue / Horizon / import workloads:
  - 4 to 8 vCPU
  - 8+ GB RAM
  - fast SSD with enough headroom for releases, logs, and backups

Operational notes:

- If database, Redis, workers, and web all run on the same VPS, RAM pressure becomes the first bottleneck.
- Offsite backups, log shipping, and metrics exporters also add background overhead.
- For serious single-node production, 2 GB RAM is usually survivable but not comfortable.

Development requirements:

- Python 3.11+.
- Docker (optional, for containerized test flow).

## Quick Start

### Empty server one-liner

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo bash
larops bootstrap init --apply
larops create site example.com --apply
```

Small VPS variant:

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --apply
```

Pin the host web stack to another PHP version on Debian-family hosts:

```bash
larops bootstrap init --php 8.4 --apply
larops create site example.com --php 8.4 --apply
```

When you pin a non-default PHP version on Ubuntu or Debian, LarOps now prepares the matching PHP package repository automatically before installing `php<major.minor>-*` packages.

Git source plus DB bootstrap:

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --apply
```

What `create site` does on a fresh host:

- If `deploy.source_base_path/<domain>` already exists, LarOps deploys from that local source tree.
- If the source directory is missing and `--git-url` is provided, LarOps clones the repository into `deploy.source_base_path/<domain>` first.
- If the source directory is missing and the effective site is Laravel-family, LarOps bootstraps the source with `composer create-project laravel/laravel`.
- If the release contains `composer.json` and `vendor/autoload.php` is missing, LarOps auto-runs `composer install` during the build phase.
- If the release contains `package.json` plus `vite.config.*` and `public/build/manifest.json` is missing, LarOps auto-runs `npm ci|install` and `npm run build` during the build phase.
- The default frontend auto-build path currently assumes an npm-managed project and preflights `package.json -> engines.node` against the host Node runtime.
- Composer build install runs with `--no-scripts`; Laravel package discovery is deferred to the app bootstrap phase after `.env` and release symlinks are ready.
- Use the same `--php` value on host bootstrap and site create so Nginx/FPM matches the installed PHP runtime.
- On Ubuntu and Debian, LarOps auto-prepares the matching external PHP repository when the pinned version is newer than the distro default.
- If `--with-db` is set, LarOps provisions the application DB/user and writes the app credential/password files before deploy.
- When the deployed source contains `artisan`, `create site` defaults to `--app-bootstrap-mode auto`:
  - it writes `APP_KEY` directly into `shared/.env` when missing
  - it only runs `migrate`, `package:discover`, and `optimize*` if the app database already appears to have schema
  - use `--app-bootstrap-mode eager` for apps known to boot safely on first create
  - use `--app-bootstrap-mode skip` when provider boot depends on schema or seeded settings
- `bootstrap init --profile small-vps` includes the local `data` stack by default. Use `--no-data` only if you intentionally keep the database off-host.
- If a previous failed create already wrote `state/apps/<domain>.json`, rerun with `--force`.

Install pinned version after a GitHub release exists:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

If the default pinned version is not published yet, the installer falls back to the latest `main` snapshot for bootstrap installs.

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

## What High-Level Commands Actually Do

This section exists to remove ambiguity. Several LarOps commands sound similar, but they do not have the same scope.

### `larops bootstrap init`

What it does:

- Optionally installs the selected host package groups:
  - `web` = `nginx`, `certbot`, PHP-FPM and core PHP extensions
  - `data` = `mariadb-server`, `redis-server`
  - `postgres` = `postgresql`
  - `ops` = `fail2ban` plus the host firewall backend (`ufw` on Debian/Ubuntu, `firewalld` on EL9)
- Writes a default config file when `--write-config` is enabled and the target file does not already exist.
- If `--domain` is provided, it can also initialize app metadata and deploy an initial release from `--source`.
- Supports `--profile small-vps` for weak servers:
  - defaults stack groups to `web + ops`
  - skips local `data` services unless you explicitly add `--data`
  - writes a more conservative runtime restart policy into generated config

What it does not do by itself:

- It does not fully replace `site create`.
- It does not automatically issue SSL certificates.
- It does not automatically enable runtime processes unless you later run site/runtime commands.
- It does not replace broader reverse-proxy, CDN, or multi-node ingress design.

Practical interpretation:

- Use `bootstrap init` to prepare a fresh host.
- Use `site create` when you want an application-oriented create flow.
- On a weak VPS, start with `larops bootstrap init --profile small-vps --apply`.
- Generated default config may include secret-file paths for disabled features; those files are only required once the matching feature is enabled.

### `larops site create`

What it does:

- Creates app metadata for the domain.
- Optionally deploys the source into a release-based layout.
- Runs deploy phases (`build`, `pre-activate`, `post-activate`, `verify`) when deploy is enabled.
- Can enable runtime processes based on presets or explicit flags.
- Supports `--profile small-vps` for a lighter Laravel default:
  - `type=laravel`
  - `cache=fastcgi`
  - `worker=false`
  - `scheduler=true`
  - `horizon=false`
- On supported single-node hosts, it provisions a managed Nginx site config automatically when deploy is enabled.
- Resolves source using this order:
  - `--source` when provided
  - otherwise `deploy.source_base_path/<domain>`
  - if that path is missing and `--git-url` is set, clone into it
  - if that path is missing and the effective site is Laravel-family, scaffold it with `composer create-project`
- Can issue Let's Encrypt certificates when `-le` is used.
- Supports `--atomic` rollback for safer first-site creation.

What it does not do:

- It is not a general-purpose package installer for the whole host.
- It assumes the host is already reasonably prepared or bootstrap has already happened.

### `larops app deploy`

What it does:

- Creates a new release from source.
- Switches `current` to the new release.
- Runs configured deploy phases and optional health/verify checks.
- Records deploy metadata and release manifest.

What it does not do:

- It does not implicitly bootstrap the host.
- It is not the same as first-time site provisioning.

### `larops security install`

What it does:

- Applies baseline host security controls:
  - host firewall allow rules for SSH/HTTP/HTTPS (`ufw` on Debian/Ubuntu, `firewalld` on EL9)
  - optional SSH rate limiting on UFW hosts
  - Fail2ban jail/filter for SSH and suspicious Nginx scan patterns

What it does not do:

- It does not harden SSH daemon policy beyond baseline firewall/jail behavior.
- It does not harden Nginx config itself.
- It is baseline security, not full host hardening.
- EL9 support is still preview-only, and some distributions may still require manual Fail2ban repo preparation.

### `larops security posture`

What it does:

- Produces a consolidated security view across:
  - firewall / Fail2ban baseline
  - `secure ssh`
  - `secure nginx`
  - monitor timers
  - Telegram notifier
  - registered app monitor timers

What it does not do:

- It does not apply any change.
- It is an inspection/report command, not a remediation command.
- On Debian-family hosts, LarOps cannot prove the server snippet is active unless you pass `--nginx-server-config-file`.
- On EL9, LarOps can verify the stock `default.d/*.conf` path through `nginx.conf` when you keep the default snippet location or pass `--nginx-root-config-file`.
- If LarOps sees managed Nginx hardening files but cannot verify activation, posture will warn instead of claiming Nginx hardening is active.
- When SELinux is active, `secure ssh` and `secure nginx` now run `restorecon -F` on LarOps-managed files before validation/reload and will fail fast if `restorecon` is unavailable.

### `larops monitor scan run`

What it does:

- Reads Nginx access log incrementally from a saved offset.
- Detects suspicious probes such as `/.env`, `/.git`, `wp-login.php`, traversal patterns, and similar paths.
- Evaluates `threshold-hits` inside a rolling `window-seconds` window.

What it does not do:

- It is not a WAF.
- It does not block traffic itself; it emits events and alerts.

### `larops site runtime enable|disable|reconcile|status`

What it does:

- Manages runtime processes for a site:
  - `worker`
  - `scheduler`
  - `horizon`
- Writes runtime specs and, when enabled, manages corresponding `systemd` units.
- `reconcile` attempts to bring configured runtime back to the expected state while respecting restart policy limits.

What it does not do:

- It does not deploy application code.
- It does not replace `app deploy`.

### `larops db offsite status`

What it does:

- Inspects encrypted offsite backup artifacts in configured object storage.
- Reports freshness and detects incomplete remote uploads.

What it does not do:

- It does not create a new backup.
- It does not verify database contents semantically; it is a storage-side inspection.

### `larops db offsite restore-verify`

What it does:

- Downloads encrypted backup artifacts from object storage.
- Validates checksum and HMAC.
- Restores into a temporary database and verifies that the artifact can actually be restored.

What it does not do:

- It does not mean your application-level data is semantically correct.
- It validates recoverability, not business correctness.

### `larops observability logs enable`

What it does:

- Configures and manages a log shipping hook using Vector.
- Ships LarOps events, Laravel logs, and Nginx logs to a configured sink.

What it does not do:

- It does not provide a log backend by itself.
- You still need an actual destination such as Vector upstream or HTTP log ingestion.

### `larops doctor metrics run`

What it does:

- Converts `doctor fleet` health into Prometheus textfile metrics.
- Lets you integrate LarOps health into node_exporter textfile collector workflows.

What it does not do:

- It is not a full monitoring platform.
- It exports health signals; it does not replace Prometheus/Grafana or alert routing.

### `larops doctor fleet`

What it does:

- Summarizes health across the host and all registered apps.
- Helps operators review runtime, backup, timers, and app health in one place.

What it does not do:

- It is not deep application tracing.
- It depends on the checks and telemetry LarOps is able to collect locally.

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

- If `--source` is omitted, LarOps first looks for `deploy.source_base_path/<domain>`.
- If that directory does not exist and `--git-url` is set, LarOps clones into it before deploy.
- If that directory does not exist and the effective site is Laravel-family, LarOps bootstraps it with `composer create-project laravel/laravel`.
- If `--with-db` is set, LarOps provisions the application DB/user and writes the app credential/password files before deploy.
- If that directory does not exist for a non-Laravel site and no `--git-url` is provided, the command fails and asks for `--source` or `--git-url`.

Create directly from Git:

```bash
larops create site example.com --git-url https://github.com/acme/example-app.git --apply
```

Bootstrap a fresh Laravel skeleton on a weak VPS:

```bash
larops create site example.com --profile small-vps --apply
```

Managed Nginx behavior:

- When deploy is enabled, `create site` provisions a managed Nginx site config by default.
- Without `-le`, the generated vhost serves HTTP.
- If a valid certificate already exists for the domain, LarOps binds HTTPS even without reissuing a new certificate.
- With `-le`, LarOps first writes the HTTP vhost, issues the certificate, then rewrites the vhost for HTTPS.
- Use `--no-nginx` if you intentionally manage ingress outside LarOps.

Where to edit application environment after create:

- Edit the shared environment file, not the release source tree:
  - LarOps auto-syncs `DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, and `DB_PASSWORD` into this file when `create site --with-db` succeeds.
  - `/var/www/<domain>/shared/.env`
- The current release usually sees:
  - `/var/www/<domain>/current/.env`
  - as a symlink into `shared/.env`
- Do not treat `.larops/state/secrets/db/<domain>.cnf` or `.txt` as the app `.env`. Those files remain LarOps-managed DB secrets and audit references.

Typical follow-up after editing `.env`:

```bash
cd /var/www/example.com/current
php artisan key:generate --force
php artisan migrate --force
php artisan optimize:clear
php artisan optimize
```

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

If `create site --with-db` was used, LarOps prints and stores:

- DB name
- DB user
- DB credential file
- DB password file

LarOps already syncs those DB values into `/var/www/<domain>/shared/.env`. You only need to add the remaining app-specific variables.

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

Named site profiles:

- `--profile small-vps`: lightweight Laravel defaults for weak servers:
  - `type=laravel`
  - `cache=fastcgi`
  - `worker=false`
  - `scheduler=true`
  - `horizon=false`
  - explicit flags still win (`--worker`, `--cache redis`, `--no-scheduler`, etc.)

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

Use this section as a quick index. Detailed examples already exist in [Standard Operations Flow](#standard-operations-flow).

- Stack and bootstrap:
  - `larops stack install --web --data --ops --apply`
  - `larops bootstrap init --apply`
- App lifecycle:
  - `larops app create example.com --apply`
  - `larops app deploy example.com --source /var/www/source/example.com --apply`
  - `larops app rollback example.com --to previous --apply`
  - `larops --json app info example.com`
- Site lifecycle:
  - `larops site create example.com --apply`
  - `larops site runtime enable example.com -w -s -a`
  - `larops site runtime reconcile example.com -w -a`
  - `larops site permissions example.com --apply`
  - `larops site delete example.com --purge --confirm example.com --apply`
- Runtime direct:
  - `larops worker status example.com`
  - `larops scheduler run-once example.com --apply`
  - `larops horizon terminate example.com --apply`
- SSL:
  - `larops ssl auto-renew enable --apply`
  - `larops ssl auto-renew disable --apply`
  - `larops ssl auto-renew status`
- Database:
  - `larops db credential show example.com`
  - `larops db backup example.com --database appdb --apply`
  - `larops db status example.com`
  - `larops db offsite status example.com`
  - `larops db verify --backup-file /path/backup.sql.gz`
  - `larops db offsite restore-verify example.com --database appdb --apply`
  - `larops db auto-backup enable example.com --database appdb --apply`
- Notifications:
  - `larops notify telegram daemon enable --apply`
  - `larops notify telegram daemon status`
  - `larops alert set --telegram-token "<token>" --telegram-chat-id "<chat-id>" --apply`
  - `larops alert test --apply`
- Security:
  - `larops security install --apply`
  - `larops security status`
  - `larops security posture`
  - `larops security report --since 24h`
  - `larops secure ssh --ssh-key-only --allow-user deploy --allow-group wheel --max-startups 10:30:60 --apply`
  - `larops secure nginx --profile strict --block-path /private/ --server-config-file /etc/nginx/sites-enabled/example.conf --apply`
- Monitoring:
  - `larops monitor scan run --threshold-hits 8 --window-seconds 300 --apply`
  - `larops monitor fim init --root /var/www/example.com/current --apply`
  - `larops monitor service run --profile laravel-host --apply`
  - `larops monitor app run example.com --apply`
  - `larops monitor scan timer enable --on-calendar "*-*-* *:*:00" --apply`
  - `larops monitor app timer enable example.com --on-calendar "*-*-* *:*:00" --apply`

Operational notes:

- Small VPS profile suggestion: scan `*-*-* *:0/2:00`, FIM `*-*-* *:0/30:00`, service watchdog `*-*-* *:*:00`
- High traffic suggestion: scan `*-*-* *:*:00`, FIM `*-*-* *:0/10:00`, service watchdog `*-*-* *:*:00`
- `monitor scan` evaluates `threshold-hits` inside a rolling `window-seconds` window.
- Built-in service watchdog profiles:
  - `laravel-host` = `nginx`, `php-fpm`, `mariadb`, `redis`
  - `laravel-postgres-host` = `nginx`, `php-fpm`, `postgresql`, `redis`

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
- Secret-file paths can safely exist in config while the feature is disabled. LarOps only reads them once the matching feature is enabled or explicitly overridden.
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

- Telegram was actually enabled, or explicit overrides forced LarOps to read `bot_token_file` / `chat_id_file`, and the file is missing or empty.

Fix:

- If Telegram is disabled, leave it disabled and the secret files do not need to exist.
- If Telegram should be enabled, create the files, add real values, set permission `600`, then retry.

### `create site` fails with "Source path does not exist or is not a directory"

Root cause:

- `--source` was omitted, `deploy.source_base_path/<domain>` does not exist yet, and LarOps could not infer how to create the source.

Fix:

- Provide a real source directory:
  - `larops create site <domain> --source /path/to/app --apply`
- Or clone from Git:
  - `larops create site <domain> --git-url https://github.com/org/repo.git --apply`
- Or use a Laravel-family site/profile so LarOps can scaffold the source automatically:
  - `larops create site <domain> --profile small-vps --apply`

### `create site` says "Application already exists. Use --force to recreate metadata."

Root cause:

- A previous `create site` run already created `state/apps/<domain>.json`, but provisioning did not finish.

Fix:

- Inspect the current metadata if needed:
  - `larops --json app info <domain>`
- If the app is not actually provisioned yet, rerun with:
  - `larops create site <domain> --force --apply`
- Reserve `--force` for recovery or deliberate recreation, not normal healthy updates.

### `larops` cannot execute after install

Root cause:

- This usually means the host still has an older install created before the virtualenv relocation fix.

Fix:

- Rerun the current installer:
  - `curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | sudo bash`
- Or rebuild `/opt/larops/.venv` in place and refresh `/usr/local/bin/larops`.

### `ssl issue` fails with `certbot` not found

Root cause:

- The host was bootstrapped with an older LarOps version before `certbot` was added to the default web stack, or `certbot` was removed manually.

Fix:

- Install the current web stack again:
  - `larops stack install --web --apply`
- Or rerun host bootstrap:
  - `larops bootstrap init --apply`
- Then retry:
  - `larops ssl issue <domain> --challenge http --apply`

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

## OS Support Matrix

Detailed OS support status and rollout guidance:

- [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)
