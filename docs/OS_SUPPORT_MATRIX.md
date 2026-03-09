# OS Support Matrix

LarOps is currently optimized for Linux hosts with `systemd`.

This matrix separates:

- `GA`: documented and intended as first-class support
- `Experimental`: expected to work with caveats, but not yet hardened to the same level
- `Planned`: realistic next targets, not implemented yet
- `Not planned`: outside the current architecture

## Current Matrix

| OS | Status | Notes |
| --- | --- | --- |
| Ubuntu 22.04 LTS | GA | Primary support target |
| Ubuntu 24.04 LTS | GA | Primary support target |
| Debian 12 | GA | Supported with Debian-family package assumptions |
| Debian 13 | Experimental | Expected to work with `apt` + `systemd`, but package naming still follows Debian-family assumptions from current stack map |
| Rocky Linux 9 | Experimental | Installer + stack package planning/install are supported via `dnf`; baseline `firewalld` + Fail2ban security flow is available experimentally |
| AlmaLinux 9 | Experimental | Same scope as Rocky Linux 9 |
| RHEL 9 | Experimental | Installer + stack package planning/install are supported via `dnf`; LarOps will attempt the EPEL + Fail2ban + firewalld path, but repo preparation can still require manual steps |
| Amazon Linux 2023 | Planned | Needs package/service abstraction review |
| Alpine Linux | Not planned | Current architecture assumes `systemd`, Debian-style package naming, and glibc-oriented host tooling |
| Arch Linux | Not planned | Current architecture is not targeting rolling-release distributions |

## Why the Current Scope Is Narrow

LarOps currently still assumes:

- `systemd` for runtime control, timers, and watchdogs
- Debian-family package naming in stack bootstrap
- Debian/Ubuntu style paths for several host integrations
- host firewall + `fail2ban` controls in the default security baseline

Code examples:

- Installer uses `apt-get`: [`scripts/install.sh`](/Volumes/Manager%20Data/Tool/larops/scripts/install.sh)
- Stack package mapping is Debian-family specific: [`src/larops/services/stack_service.py`](/Volumes/Manager%20Data/Tool/larops/src/larops/services/stack_service.py)
- Runtime and monitoring rely heavily on `systemd`: [`src/larops/config.py`](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py)

## What "Experimental" Means for Debian 13

Debian 13 is treated as a reasonable next target because:

- it still fits the `apt + systemd` model
- much of the existing host integration should remain compatible

But it is not yet promoted to GA because LarOps still hard-codes Debian-family package names such as:

- `php8.3-fpm`
- `php8.3-cli`
- `php8.3-mbstring`
- `mariadb-server`
- `redis-server`

If those package names differ on a given Debian 13 host, `stack install` / `bootstrap init` can still fail.

## What Still Blocks Full RHEL-Family Parity

Rocky / Alma / RHEL 9 now have experimental installer and stack support, but LarOps still needs these layers before they should be promoted beyond experimental:

1. OS detection
2. Package manager abstraction (`apt` vs `dnf`)
3. Package map abstraction per distro family
4. Firewall abstraction (`ufw` vs `firewalld`)
5. Service-name abstraction
6. Path/layout abstraction for logs and config files
7. SELinux-aware operational guidance

## Recommended Rollout Order

1. Harden Debian 13 from experimental to GA
2. Review SELinux-aware operational flows for RHEL-family hosts
3. Improve package/repo guidance for RHEL-family Fail2ban installs
4. Evaluate Amazon Linux 2023 separately

## Practical Guidance Today

If you want the least-risk production path today, use:

- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS
- Debian 12

If you want to evaluate the next likely target, test on:

- Debian 13
- Rocky Linux 9
- AlmaLinux 9
- RHEL 9

If you want to force-install on an unsupported OS anyway, the installer supports:

```bash
LAROPS_ALLOW_UNSUPPORTED_OS=true
```

That only bypasses installer gating. It does **not** mean the platform is truly supported.
