# Production Runbook (Ubuntu 22.04/24.04)

## 1) Install pinned LarOps release

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

## 2) Bootstrap base stack

```bash
sudo larops bootstrap init --apply
```

## 3) Configure LarOps

Edit `/etc/larops/larops.yaml`:

```yaml
environment: production
state_path: /var/lib/larops/state
deploy:
  releases_path: /var/www
  source_base_path: /var/www/source
  keep_releases: 5
  health_check_path: /up
systemd:
  manage: true
  unit_dir: /etc/systemd/system
  user: www-data
events:
  sink: jsonl
  path: /var/log/larops/events.jsonl
notifications:
  telegram:
    enabled: true
    bot_token: ""
    bot_token_file: /etc/larops/secrets/telegram_bot_token
    chat_id: ""
    chat_id_file: /etc/larops/secrets/telegram_chat_id
    min_severity: error
    batch_size: 20
```

Create runtime paths:

```bash
sudo install -d -m 755 /var/log/larops /var/lib/larops/state
```

## 4) Configure Telegram secrets

```bash
sudo install -d -m 700 /etc/larops/secrets
echo "123456:BOT_TOKEN" | sudo tee /etc/larops/secrets/telegram_bot_token >/dev/null
echo "-1001234567890" | sudo tee /etc/larops/secrets/telegram_chat_id >/dev/null
sudo chmod 600 /etc/larops/secrets/telegram_bot_token /etc/larops/secrets/telegram_chat_id
```

Create systemd env file:

```bash
sudo tee /etc/larops/telegram.env >/dev/null <<'ENV'
LAROPS_TELEGRAM_ENABLED=true
LAROPS_TELEGRAM_BOT_TOKEN_FILE=/etc/larops/secrets/telegram_bot_token
LAROPS_TELEGRAM_CHAT_ID_FILE=/etc/larops/secrets/telegram_chat_id
ENV
sudo chmod 600 /etc/larops/telegram.env
```

## 5) App lifecycle

```bash
sudo larops --config /etc/larops/larops.yaml app create example.com --apply
sudo larops --config /etc/larops/larops.yaml app deploy example.com --source /var/www/source --ref main --apply
```

Or short command (create + deploy):

```bash
sudo larops --config /etc/larops/larops.yaml create site example.com --apply
sudo larops --config /etc/larops/larops.yaml create site example.com -le --le-email ops@example.com --apply
sudo larops --config /etc/larops/larops.yaml site create example.com -a
sudo larops --config /etc/larops/larops.yaml site runtime disable example.com -a
sudo larops --config /etc/larops/larops.yaml site runtime status example.com
```

Enable runtime:

```bash
sudo larops --config /etc/larops/larops.yaml worker enable example.com --queue default --concurrency 2 --apply
sudo larops --config /etc/larops/larops.yaml scheduler enable example.com --apply
sudo larops --config /etc/larops/larops.yaml horizon enable example.com --apply
```

## 6) DB credential + backup

```bash
export LAROPS_DB_PASSWORD='strong-db-password'
sudo --preserve-env=LAROPS_DB_PASSWORD larops --config /etc/larops/larops.yaml \
  db credential set example.com --user appuser --host 127.0.0.1 --port 3306 --apply

sudo larops --config /etc/larops/larops.yaml db backup example.com --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db list-backups example.com
```

## 7) Telegram event-stream daemon

```bash
sudo larops --config /etc/larops/larops.yaml notify telegram daemon enable --apply
sudo larops --config /etc/larops/larops.yaml notify telegram daemon status
```

Smoke test:

```bash
sudo larops --config /etc/larops/larops.yaml notify telegram test --apply
```

## 8) Health checks

```bash
sudo larops --config /etc/larops/larops.yaml doctor quick host
sudo larops --config /etc/larops/larops.yaml doctor run example.com
```

## 9) Release update procedure

```bash
git pull
scripts/release.sh 0.2.0
git push origin main
git push origin v0.2.0
```

Upgrade server:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.2.0 bash
```
