# Production Runbook (Ubuntu 22.04/24.04, Debian 12)

This runbook is written for the current GA production targets.
If you are on Debian 13 or EL9-family hosts, treat the same commands as preview/evaluation paths and validate them on a real host before standardizing them.

## 1) Install LarOps

Bootstrap install:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo bash
```

Pinned install after a GitHub release exists:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

## 2) Bootstrap base stack

```bash
sudo larops bootstrap init --apply
```

For a weak VPS that should avoid local DB/Redis by default:

```bash
sudo larops bootstrap init --profile small-vps --apply
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

## 5) Configure backup encryption + offsite storage

Create offsite secrets:

```bash
sudo install -d -m 700 /etc/larops/secrets
openssl rand -base64 48 | sudo tee /etc/larops/secrets/backup_passphrase >/dev/null
echo "R2_OR_S3_ACCESS_KEY" | sudo tee /etc/larops/secrets/offsite_access_key_id >/dev/null
echo "R2_OR_S3_SECRET_KEY" | sudo tee /etc/larops/secrets/offsite_secret_access_key >/dev/null
sudo chmod 600 /etc/larops/secrets/backup_passphrase /etc/larops/secrets/offsite_access_key_id /etc/larops/secrets/offsite_secret_access_key
```

Example config for Cloudflare R2 / S3-compatible object storage:

```yaml
backups:
  encryption:
    enabled: true
    passphrase_file: /etc/larops/secrets/backup_passphrase
    cipher: aes-256-cbc
  offsite:
    enabled: true
    provider: s3
    bucket: larops-backups
    prefix: production
    region: auto
    endpoint_url: https://<accountid>.r2.cloudflarestorage.com
    access_key_id_file: /etc/larops/secrets/offsite_access_key_id
    secret_access_key_file: /etc/larops/secrets/offsite_secret_access_key
    storage_class: STANDARD
    retention_days: 30
    stale_hours: 24
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
sudo larops --config /etc/larops/larops.yaml ssl auto-renew enable --apply
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

Check auto-renew timer status:

```bash
sudo larops --config /etc/larops/larops.yaml ssl auto-renew status
```

## 6) DB credential + backup

```bash
export LAROPS_DB_PASSWORD='strong-db-password'
sudo --preserve-env=LAROPS_DB_PASSWORD larops --config /etc/larops/larops.yaml \
  db credential set example.com --user appuser --host 127.0.0.1 --port 3306 --apply

sudo larops --config /etc/larops/larops.yaml db backup example.com --database appdb --retain-count 10 --apply
sudo larops --config /etc/larops/larops.yaml db status example.com
sudo larops --config /etc/larops/larops.yaml db offsite status example.com
sudo larops --config /etc/larops/larops.yaml db verify --backup-file /path/backup.sql.gz
sudo larops --config /etc/larops/larops.yaml db restore-verify example.com --backup-file /path/backup.sql.gz --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db offsite restore-verify example.com --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db auto-backup enable example.com --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db auto-backup status example.com
sudo larops --config /etc/larops/larops.yaml db list-backups example.com
```

Notes:

- Offsite uploads are client-side encrypted before upload and include an HMAC in the manifest for restore-time tamper detection.
- `db offsite status` returns `error` if the latest remote upload is incomplete, for example when a `.enc` object exists without its `.enc.json` manifest pair.

PostgreSQL variant:

```bash
export LAROPS_DB_PASSWORD='strong-db-password'
sudo --preserve-env=LAROPS_DB_PASSWORD larops --config /etc/larops/larops.yaml \
  db credential set example.com --engine postgres --user appuser --host 127.0.0.1 --port 5432 --apply

sudo larops --config /etc/larops/larops.yaml db backup example.com --engine postgres --database appdb --retain-count 10 --apply
sudo larops --config /etc/larops/larops.yaml db restore-verify example.com --engine postgres --backup-file /path/backup.sql.gz --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db offsite restore-verify example.com --engine postgres --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db auto-backup enable example.com --engine postgres --database appdb --apply
sudo larops --config /etc/larops/larops.yaml db restore example.com --engine postgres --backup-file /path/backup.sql.gz --database appdb --apply
```

## 7) Telegram event-stream daemon

```bash
sudo larops --config /etc/larops/larops.yaml notify telegram daemon enable --apply
sudo larops --config /etc/larops/larops.yaml notify telegram daemon status
sudo larops --config /etc/larops/larops.yaml secure ssh --ssh-key-only --apply
sudo larops --config /etc/larops/larops.yaml secure ssh \
  --ssh-key-only \
  --allow-user deploy \
  --allow-group wheel \
  --max-startups 10:30:60 \
  --apply

# Debian / Ubuntu
sudo larops --config /etc/larops/larops.yaml secure nginx \
  --server-config-file /etc/nginx/sites-enabled/example.com.conf \
  --apply
sudo larops --config /etc/larops/larops.yaml secure nginx \
  --profile strict \
  --block-path /private/ \
  --server-config-file /etc/nginx/sites-enabled/example.com.conf \
  --apply
sudo larops --config /etc/larops/larops.yaml security posture \
  --nginx-server-config-file /etc/nginx/sites-enabled/example.com.conf

# EL9 (Rocky / Alma / RHEL 9)
sudo larops --config /etc/larops/larops.yaml secure nginx --apply
sudo larops --config /etc/larops/larops.yaml secure nginx \
  --profile strict \
  --block-path /private/ \
  --nginx-root-config-file /etc/nginx/nginx.conf \
  --apply
sudo larops --config /etc/larops/larops.yaml security posture \
  --nginx-root-config-file /etc/nginx/nginx.conf
```

Note: on SELinux-enforcing hosts, `secure ssh` and `secure nginx` automatically run `restorecon -F` on LarOps-managed files before validation/reload. Keep `policycoreutils`/`restorecon` available on EL9 hosts.

Smoke test:

```bash
sudo larops --config /etc/larops/larops.yaml notify telegram test --apply
```

## 8) Security monitor profiles (scan + FIM + critical service watchdog)

Prerequisite: keep `notify telegram daemon` enabled so watchdog events become Telegram alerts.

Initialize baseline once (required before FIM timer):

```bash
sudo larops --config /etc/larops/larops.yaml monitor fim init \
  --root /var/www/example.com/current \
  --baseline-file /var/lib/larops/state/security/fim_baseline.json \
  --apply
```

Profile A - Small VPS (lower load):

```bash
# Scan every 2 minutes
sudo larops --config /etc/larops/larops.yaml monitor scan timer enable \
  --on-calendar "*-*-* *:0/2:00" \
  --randomized-delay 15 \
  --threshold-hits 8 \
  --window-seconds 300 \
  --max-lines 5000 \
  --top 10 \
  --apply

# FIM every 30 minutes
sudo larops --config /etc/larops/larops.yaml monitor fim timer enable \
  --on-calendar "*-*-* *:0/30:00" \
  --randomized-delay 120 \
  --baseline-file /var/lib/larops/state/security/fim_baseline.json \
  --apply

# Service watchdog every minute
sudo larops --config /etc/larops/larops.yaml monitor service timer enable \
  --profile laravel-host \
  --on-calendar "*-*-* *:*:00" \
  --randomized-delay 10 \
  --restart-cooldown 300 \
  --apply
```

Profile B - High traffic (higher frequency):

```bash
# Scan every 1 minute with larger window
sudo larops --config /etc/larops/larops.yaml monitor scan timer enable \
  --on-calendar "*-*-* *:*:00" \
  --randomized-delay 5 \
  --threshold-hits 20 \
  --window-seconds 120 \
  --max-lines 20000 \
  --top 20 \
  --apply

# FIM every 10 minutes
sudo larops --config /etc/larops/larops.yaml monitor fim timer enable \
  --on-calendar "*-*-* *:0/10:00" \
  --randomized-delay 60 \
  --baseline-file /var/lib/larops/state/security/fim_baseline.json \
  --apply

# Service watchdog every minute
sudo larops --config /etc/larops/larops.yaml monitor service timer enable \
  --profile laravel-host \
  --on-calendar "*-*-* *:*:00" \
  --randomized-delay 5 \
  --restart-cooldown 180 \
  --apply
```

Check timer status:

```bash
sudo larops --config /etc/larops/larops.yaml monitor scan timer status
sudo larops --config /etc/larops/larops.yaml monitor fim timer status
sudo larops --config /etc/larops/larops.yaml monitor service timer status
sudo larops --config /etc/larops/larops.yaml monitor app timer status example.com
```

Review security report with time window:

```bash
sudo larops --config /etc/larops/larops.yaml security report --since 1h
sudo larops --config /etc/larops/larops.yaml security report --since 24h
```

`monitor scan` evaluates `threshold-hits` inside `window-seconds`, not just per timer invocation. Keep the window slightly larger than the timer cadence if you want stable spike detection during timer jitter or uneven log bursts.

## 9) Health checks

```bash
sudo larops --config /etc/larops/larops.yaml doctor quick host
sudo larops --config /etc/larops/larops.yaml doctor run example.com
sudo larops --config /etc/larops/larops.yaml doctor fleet
sudo larops --config /etc/larops/larops.yaml doctor fleet --quick --include-checks
sudo larops --config /etc/larops/larops.yaml doctor metrics run
sudo larops --config /etc/larops/larops.yaml doctor metrics run \
  --output-file /var/lib/node_exporter/textfile_collector/larops.prom \
  --apply
sudo larops --config /etc/larops/larops.yaml doctor metrics timer enable \
  --output-file /var/lib/node_exporter/textfile_collector/larops.prom \
  --apply
sudo larops --config /etc/larops/larops.yaml observability logs enable \
  --sink vector \
  --vector-address 10.0.0.10:6000 \
  --apply
sudo larops --config /etc/larops/larops.yaml observability logs enable \
  --sink http \
  --http-uri https://logs.example.com/ingest \
  --http-env-file /etc/larops/vector-http.env \
  --apply
sudo larops --config /etc/larops/larops.yaml observability logs status
sudo larops --config /etc/larops/larops.yaml worker reconcile example.com --apply
sudo larops --config /etc/larops/larops.yaml scheduler reconcile example.com --apply
```

Recommended app probes:

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

Turn these probes into alerts:

```bash
sudo larops --config /etc/larops/larops.yaml monitor app run example.com --apply
sudo larops --config /etc/larops/larops.yaml monitor app timer enable \
  example.com \
  --on-calendar "*-*-* *:*:00" \
  --randomized-delay 10 \
  --apply
```

## 10) Release update procedure

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
