# larops

Laravel-first server operations CLI.

## Purpose

LarOps standardizes Laravel infrastructure operations:

1. Stack provisioning (`nginx`, `php-fpm`, `mysql/mariadb`, `redis`, `supervisor`)
2. Application lifecycle (`create`, `deploy`, `rollback`)
3. Runtime controls (`worker`, `scheduler`, `horizon`)
4. Operability (`ssl`, `backup`, `doctor`, event stream)

## Current Stage

Bootstrap stage: CLI foundation and event pipeline skeleton.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
larops --help
pytest
```

## Roadmap

1. S1: extraction report and reusable provisioning patterns
2. S2: core command framework and lock manager
3. S3+: deploy lifecycle, runtime controls, observability, notifications

