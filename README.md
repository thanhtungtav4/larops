# LarOps

Laravel-first server operations CLI for provisioning, deployment, runtime control, backups, security hardening, and observability on Linux servers.

## Language

- English: [README.en.md](README.en.md)
- Tiếng Việt: [README.vi.md](README.vi.md)

## What LarOps Covers

- Host bootstrap (`stack install`, `bootstrap init`)
- Site and app lifecycle (`site create`, `app deploy`, `app rollback`)
- Runtime control for `worker`, `scheduler`, and `horizon`
- SSL issuance and renewal
- Database backup, restore, offsite backup, and restore verification
- Security baseline, SSH/Nginx hardening, and scan/FIM monitoring
- Telegram notifications, fleet health checks, metrics export, and log shipping hooks

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo bash
larops bootstrap init --apply
larops create site example.com --apply
```

## Recommended Reading Order

1. Start with [README.en.md](README.en.md) if your team operates primarily in English.
2. Start with [README.vi.md](README.vi.md) if you want the same workflow explained in Vietnamese.
3. Use [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md) for production-oriented checklists and command sequences.
4. Use [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md) before targeting a new distro.

## Core Principles

- `plan` first, `--apply` second
- Prefer secret files over inline credentials
- Validate deploys with health and verify phases
- Treat backup as incomplete until `restore-verify` passes
- Treat security posture as incomplete until hardening, timers, and notifier are all enabled

## Production Notes

- LarOps is strongest today for serious single-node Laravel operations.
- Official production OS targets are Ubuntu 22.04/24.04 and Debian 12.
- Debian 13 and EL9-family hosts should currently be treated as preview/evaluation targets, not primary production targets.
- Multi-node orchestration and HA are outside the current core scope.
- Use pinned releases in production instead of unpinned installer flows.

## Repository Docs

- English manual: [README.en.md](README.en.md)
- Vietnamese manual: [README.vi.md](README.vi.md)
- Production runbook: [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)
- OS support matrix: [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
