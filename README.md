# larops

Laravel-first server operations CLI.

## Purpose

LarOps standardizes Laravel infrastructure operations:

1. Stack provisioning (`nginx`, `php-fpm`, `mysql/mariadb`, `redis`, `supervisor`)
2. Application lifecycle (`create`, `deploy`, `rollback`)
3. Runtime controls (`worker`, `scheduler`, `horizon`)
4. Operability (`ssl`, `backup`, `doctor`, event stream)

## Current Stage

S3 foundation: runtime context, stack planner, and app lifecycle (`create`, `deploy`, `rollback`, `info`).

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
YAML

larops --config /tmp/larops.yaml app create demo.test --apply
larops --config /tmp/larops.yaml app deploy demo.test --source . --apply
larops --config /tmp/larops.yaml app rollback demo.test --to previous --apply
larops --config /tmp/larops.yaml --json app info demo.test
```

## Docker Test

```bash
docker compose build
docker compose run --rm larops-test
docker compose run --rm larops-cli
```

## Roadmap

1. S1: extraction report and reusable provisioning patterns
2. S2: core command framework and lock manager
3. S3+: deploy lifecycle, runtime controls, observability, notifications
