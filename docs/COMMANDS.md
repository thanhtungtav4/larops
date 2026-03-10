# LarOps Commands

Fast command index for LarOps. This page is intentionally short.

For full operating details:

- English manual: [../README.en.md](../README.en.md)
- Vietnamese manual: [../README.vi.md](../README.vi.md)
- Production runbook: [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md)
- Troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## Syntax

```bash
larops [global options] <command-group> <subcommand> [options]
```

Common global options:

- `--config /path/to/larops.yaml`
- `--json`
- `--dry-run`
- `--verbose`

## Command Overview

| Group | Purpose | Typical example |
| --- | --- | --- |
| `stack` | Install host package groups (`web`, `data`, `ops`, `postgres`) | `larops stack install --web --ops --apply` |
| `bootstrap` | Bootstrap a fresh host and optionally seed the first app release | `larops bootstrap init --profile small-vps --apply` |
| `create` | Shortcuts for first-time site creation, source bootstrap, managed Nginx site provisioning, and optional DB bootstrap | `larops create site example.com --git-url https://github.com/acme/app.git --with-db --apply` |
| `site` | Site-oriented lifecycle operations | `larops site runtime enable example.com -w -s -a` |
| `app` | Release-based app lifecycle (`create`, `deploy`, `rollback`, `bootstrap`, `info`) | `larops app bootstrap example.com --seed --apply` |
| `worker` | Queue worker runtime control | `larops worker enable example.com --queue default --concurrency 2 --apply` |
| `scheduler` | Scheduler runtime control | `larops scheduler enable example.com --apply` |
| `horizon` | Horizon runtime control | `larops horizon enable example.com --apply` |
| `ssl` | Issue, renew, and inspect certificates | `larops ssl issue example.com --challenge http --apply` |
| `db` | DB credentials, provisioning, backup, restore, offsite backup, verification | `larops db provision example.com --apply` |
| `notify` | Telegram event-stream daemon lifecycle | `larops notify telegram daemon enable --apply` |
| `alert` | Configure and test alert channels | `larops alert test --apply` |
| `security` | Baseline host security, reporting, and posture checks | `larops security posture` |
| `secure` | Preventive SSH and Nginx hardening | `larops secure ssh --ssh-key-only --apply` |
| `monitor` | Scan/FIM/service/app monitoring and timers | `larops monitor service timer enable --profile laravel-host --apply` |
| `doctor` | Host/app/fleet health checks and metrics export | `larops doctor fleet --include-checks` |
| `observability` | Log shipping hooks | `larops observability logs enable --sink vector --vector-address 10.0.0.10:6000 --apply` |

## High-Level Flows

### Fresh host

```bash
larops bootstrap init --apply
larops create site example.com --apply
```

What you get on supported single-node hosts:

- release-based deploy
- managed Nginx site config
- HTTP service immediately, or HTTPS in the same flow when `-le` is used

### Weak VPS

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --apply
```

### Existing app source

```bash
larops create site example.com --source /var/www/source/example.com --apply
```

### Clone from Git

```bash
larops create site example.com --git-url https://github.com/acme/example-app.git --apply
```

### Clone from Git and provision DB

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --apply
```

### Clone from Git and force eager Laravel bootstrap

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --app-bootstrap-mode eager \
  --apply
```

### Clone from Git and skip Laravel bootstrap on first create

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --app-bootstrap-mode skip \
  --apply

### Bootstrap an already provisioned app after schema or DB settings are ready

```bash
larops app bootstrap example.com --apply
```

### Bootstrap and seed an already provisioned app

```bash
larops app bootstrap example.com --seed --seeder-class DemoSeeder --apply
```
```

### Clone from Git and issue Let's Encrypt in the same flow

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  -le \
  --le-email ops@example.com \
  --apply
```

## `create site` Source Rules

When `larops create site <domain>` runs:

1. If `--source` is provided, LarOps uses that directory.
2. Otherwise LarOps looks for `deploy.source_base_path/<domain>`.
3. If that directory is missing and `--git-url` is provided, LarOps clones into it.
4. If that directory is missing and the effective site is Laravel-family, LarOps bootstraps it with `composer create-project laravel/laravel`.
5. If `--with-db` is set and the effective site has a real DB engine, LarOps provisions the application DB/user and writes the app credential/password files.
6. If the release contains `composer.json` but is missing `vendor/autoload.php`, LarOps runs `composer install --no-scripts` during the build phase.
7. If the release contains `package.json` plus `vite.config.*` and `public/build/manifest.json` is missing, LarOps runs `npm ci|install` and `npm run build` during the build phase. This default path currently targets npm-managed projects and preflights `package.json -> engines.node`.
8. If the deployed source contains `artisan`, `create site` defaults to `--app-bootstrap-mode auto`:
   - write `APP_KEY` directly into `shared/.env` when missing
   - run `migrate`, `package:discover`, and `optimize*` only when the app database already appears to have schema
   - if LarOps cannot determine safe DB context, it skips Laravel bootstrap instead of forcing artisan commands on first create
   - use `--app-bootstrap-mode eager` for known-safe apps, or `--app-bootstrap-mode skip` to skip Laravel bootstrap on first create
9. After Nginx provisioning, `create site` prints lightweight smoke results such as `smoke http: 301` and `smoke https: 200`.
10. If a previous failed create already wrote app metadata, rerun with `--force`.

## `app bootstrap` Rules

Use `larops app bootstrap <domain>` after the site already exists and you want LarOps to run the Laravel bootstrap steps in a controlled way.

What it does:

- re-syncs `DB_*` into `shared/.env` from `database_provision` when LarOps has DB metadata
- writes `APP_KEY` directly into `shared/.env` if it is missing
- reapplies writable permissions for `storage` and `bootstrap/cache`
- runs the selected artisan steps on the current release

Default artisan sequence:

1. `php artisan migrate --force`
2. `php artisan package:discover --ansi`
3. `php artisan optimize:clear`
4. `php artisan optimize`

Optional flags:

- `--seed`: add `php artisan db:seed --force`
- `--seeder-class <ClassName>`: seed with a specific class
- `--skip-migrate`
- `--skip-package-discover`
- `--skip-optimize`

`bootstrap init --profile small-vps` includes the local `data` stack by default. Use `--no-data` only if you intentionally want an off-host database.

On Ubuntu and Debian, `bootstrap init --php <major.minor>` and `stack install --web --php <major.minor>` pin the host PHP version and auto-prepare the matching external PHP package repository when needed.

## Production Scope

Production-supported operating systems:

- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- Debian 12

Preview / evaluation only:

- Debian 13
- Rocky Linux 9
- AlmaLinux 9
- RHEL 9

See [OS_SUPPORT_MATRIX.md](OS_SUPPORT_MATRIX.md) before standardizing a non-GA host.
