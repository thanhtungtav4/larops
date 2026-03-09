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
| `create` | Shortcuts for first-time site creation, source bootstrap, and managed Nginx site provisioning | `larops create site example.com --git-url https://github.com/acme/app.git --apply` |
| `site` | Site-oriented lifecycle operations | `larops site runtime enable example.com -w -s -a` |
| `app` | Release-based app lifecycle (`create`, `deploy`, `rollback`, `info`) | `larops app deploy example.com --source /var/www/source/example.com --apply` |
| `worker` | Queue worker runtime control | `larops worker enable example.com --queue default --concurrency 2 --apply` |
| `scheduler` | Scheduler runtime control | `larops scheduler enable example.com --apply` |
| `horizon` | Horizon runtime control | `larops horizon enable example.com --apply` |
| `ssl` | Issue, renew, and inspect certificates | `larops ssl issue example.com --challenge http --apply` |
| `db` | DB credentials, backup, restore, offsite backup, verification | `larops db backup example.com --database appdb --apply` |
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

## `create site` Source Rules

When `larops create site <domain>` runs:

1. If `--source` is provided, LarOps uses that directory.
2. Otherwise LarOps looks for `deploy.source_base_path/<domain>`.
3. If that directory is missing and `--git-url` is provided, LarOps clones into it.
4. If that directory is missing and the effective site is Laravel-family, LarOps bootstraps it with `composer create-project laravel/laravel`.
5. If a previous failed create already wrote app metadata, rerun with `--force`.

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
