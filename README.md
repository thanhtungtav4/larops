# LarOps

Laravel-first server operations CLI for provisioning, deployment, runtime control, backups, security hardening, and observability on Linux hosts.

## Languages

- English manual: [README.en.md](README.en.md)
- Tiếng Việt: [README.vi.md](README.vi.md)

## Production Scope

Primary production targets:

- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- Debian 12

Preview / evaluation only:

- Debian 13
- Rocky Linux 9
- AlmaLinux 9
- RHEL 9

Details: [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo bash
larops bootstrap init --apply
larops create site example.com --apply
```

Git source + DB bootstrap:

```bash
larops create site example.com \
  --git-url https://github.com/acme/app.git \
  --with-db \
  --apply
```

Weak VPS:

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --apply
```

Weak VPS with local DB:

```bash
larops bootstrap init --profile small-vps --data --apply
larops create site example.com --profile small-vps --with-db --apply
```

## Command Overview

| Group | Purpose | Example |
| --- | --- | --- |
| `stack` | Install host package groups | `larops stack install --web --ops --apply` |
| `bootstrap` | Bootstrap a fresh host | `larops bootstrap init --apply` |
| `create` | First-time site create shortcut | `larops create site example.com --apply` |
| `site` | Site-oriented lifecycle operations | `larops site runtime enable example.com -w -s -a` |
| `app` | Release-based deploy and rollback | `larops app deploy example.com --source /var/www/source/example.com --apply` |
| `worker`, `scheduler`, `horizon` | Runtime process control | `larops worker enable example.com --queue default --apply` |
| `ssl` | Certificate issue, renew, check | `larops ssl issue example.com --challenge http --apply` |
| `db` | Credentials, backup, restore, offsite | `larops db backup example.com --database appdb --apply` |
| `notify`, `alert` | Telegram daemon and alert setup | `larops notify telegram daemon enable --apply` |
| `security`, `secure`, `monitor` | Baseline security, hardening, monitoring | `larops security posture` |
| `doctor`, `observability` | Health, metrics, and log shipping | `larops doctor fleet --include-checks` |

Full command index: [docs/COMMANDS.md](docs/COMMANDS.md)

## Fresh-Host Behavior

- If the default pinned release asset has not been published yet, the installer falls back to the latest `main` snapshot for the bootstrap install.
- Disabled integrations can keep secret-file paths in `/etc/larops/larops.yaml`; those files are only required once the matching feature is enabled.
- `create site` supports three source modes:
  - existing local source directory
  - Git clone with `--git-url`
  - automatic Laravel scaffold with `composer create-project` when the site resolves to a Laravel-family profile
- `create site --with-db` can provision the application database, user, credential file, and password file in the same flow.
- When deploy is enabled, `create site` also provisions a managed Nginx site config by default on supported single-node hosts.
- If a previous `create site` run created metadata but did not finish provisioning, rerun with `--force`.

## Docs Map

- Command index: [docs/COMMANDS.md](docs/COMMANDS.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Production runbook: [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)
- English manual: [README.en.md](README.en.md)
- Vietnamese manual: [README.vi.md](README.vi.md)
- OS support matrix: [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
