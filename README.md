# larops

Laravel-first server operations CLI.

## Purpose

LarOps standardizes Laravel infrastructure operations:

1. Stack provisioning (`nginx`, `php-fpm`, `mysql/mariadb`, `redis`, `supervisor`)
2. Application lifecycle (`create`, `deploy`, `rollback`)
3. Runtime controls (`worker`, `scheduler`, `horizon`)
4. Operability (`ssl`, `backup`, `doctor`, event stream)

## Current Stage

S6 foundation: WordOps-style bootstrap flow, app lifecycle, runtime process controls, SSL lifecycle, DB backup/restore, release flow, and Telegram event-stream adapter (including systemd daemon mode).

## Empty Server One-Liner

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | sudo bash
larops bootstrap init --apply
```

Install pinned release:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

Optional app bootstrap in one go:

```bash
larops bootstrap init --domain example.com --source /var/www/source --apply
```

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
larops --help
pytest
```

## App Lifecycle Example

```bash
cat > /tmp/larops.yaml <<'YAML'
environment: test
state_path: /tmp/larops-state
deploy:
  releases_path: /tmp/larops-apps
  keep_releases: 3
  health_check_path: /up
events:
  sink: jsonl
  path: /tmp/larops-events.jsonl
systemd:
  manage: false
  unit_dir: /tmp/larops-systemd
  user: www-data
notifications:
  telegram:
    enabled: true
    bot_token: ""
    bot_token_file: /etc/larops/secrets/telegram_bot_token
    chat_id: ""
    chat_id_file: /etc/larops/secrets/telegram_chat_id
    min_severity: error
    batch_size: 20
YAML

larops --config /tmp/larops.yaml app create demo.test --apply
larops --config /tmp/larops.yaml app deploy demo.test --source . --apply
larops --config /tmp/larops.yaml app rollback demo.test --to previous --apply
larops --config /tmp/larops.yaml --json app info demo.test

# Runtime process control (systemd real mode when systemd.manage=true)
larops --config /tmp/larops.yaml worker enable demo.test --queue default --concurrency 2 --apply
larops --config /tmp/larops.yaml scheduler enable demo.test --apply
larops --config /tmp/larops.yaml horizon enable demo.test --apply
larops --config /tmp/larops.yaml --json worker status demo.test

# SSL lifecycle
larops --config /tmp/larops.yaml ssl issue demo.test --challenge http

# DB credential + backup/restore
export LAROPS_DB_PASSWORD="strong-password"
larops --config /tmp/larops.yaml db credential set demo.test --user appuser --apply
larops --config /tmp/larops.yaml db backup demo.test --database appdb --apply
larops --config /tmp/larops.yaml db list-backups demo.test

# Telegram adapter from event stream
larops --config /tmp/larops.yaml notify telegram run-once --apply
larops --config /tmp/larops.yaml notify telegram daemon enable --apply

# Health checks
larops --config /tmp/larops.yaml --json doctor run demo.test
```

## Telegram Secrets and Daemon Mode

Create secret files:

```bash
sudo install -d -m 700 /etc/larops/secrets
echo "123456:BOT_TOKEN" | sudo tee /etc/larops/secrets/telegram_bot_token >/dev/null
echo "-1001234567890" | sudo tee /etc/larops/secrets/telegram_chat_id >/dev/null
sudo chmod 600 /etc/larops/secrets/telegram_bot_token /etc/larops/secrets/telegram_chat_id
```

Optional env file for daemon (used by systemd unit):

```bash
sudo tee /etc/larops/telegram.env >/dev/null <<'ENV'
LAROPS_TELEGRAM_ENABLED=true
LAROPS_TELEGRAM_BOT_TOKEN_FILE=/etc/larops/secrets/telegram_bot_token
LAROPS_TELEGRAM_CHAT_ID_FILE=/etc/larops/secrets/telegram_chat_id
ENV
sudo chmod 600 /etc/larops/telegram.env
```

Enable/inspect daemon:

```bash
larops --config /etc/larops/larops.yaml notify telegram daemon enable --apply
larops --config /etc/larops/larops.yaml notify telegram daemon status
```

## Docker Test

```bash
docker compose build
docker compose run --rm larops-test
docker compose run --rm larops-cli
```

## Installer Script

Direct install script is in `scripts/install.sh`.  
It installs dependencies, clones/updates source, installs LarOps into a venv, links `/usr/local/bin/larops`, and seeds `/etc/larops/larops.yaml`.

## Release Flow

1. Ensure clean git tree.
2. Run `scripts/release.sh <semver>` (for example `scripts/release.sh 0.2.0`).
3. Push release commit and tag:

```bash
git push origin main
git push origin v0.2.0
```

4. Install pinned release on server by setting `LAROPS_VERSION`.

## Production Runbook

Detailed production runbook: [docs/PRODUCTION_RUNBOOK.md](/Volumes/Manager%20Data/Tool/larops/docs/PRODUCTION_RUNBOOK.md)
