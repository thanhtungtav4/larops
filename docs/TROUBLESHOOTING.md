# LarOps Troubleshooting

Short operational fixes for common LarOps failures.

For full manual pages:

- English manual: [../README.en.md](../README.en.md)
- Vietnamese manual: [../README.vi.md](../README.vi.md)
- Production runbook: [PRODUCTION_RUNBOOK.md](PRODUCTION_RUNBOOK.md)

## Installer falls back from the default pinned version

Symptom:

```text
[larops-install] Using pinned version v0.1.0...
curl: (22) The requested URL returned error: 404
```

Meaning:

- The default pinned GitHub release asset was not published yet.
- Current installer behavior is to fall back to the latest `main` snapshot for the initial bootstrap install.

What to do:

- Usually nothing. The fallback path is expected for first install before a formal release exists.
- For production pinning, use a real published release:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=x.y.z bash
```

## `larops` cannot execute after install

Symptom:

```text
-bash: /usr/local/bin/larops: cannot execute: required file not found
```

Meaning:

- The host still has an older install created before the virtualenv relocation fix.

Fix:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | sudo bash
```

Or repair in place:

```bash
sudo rm -rf /opt/larops/.venv
sudo python3 -m venv /opt/larops/.venv
sudo /opt/larops/.venv/bin/pip install --upgrade pip
sudo /opt/larops/.venv/bin/pip install -e /opt/larops
sudo ln -sf /opt/larops/.venv/bin/larops /usr/local/bin/larops
```

## Config error from Telegram secret file

Symptom:

```text
Config error: Telegram bot token file not found: /etc/larops/secrets/telegram_bot_token
```

Meaning:

- Telegram was actually enabled, or explicit overrides forced LarOps to read the token/chat-id files.
- Disabled features can keep secret-file paths in config without requiring those files to exist.

Fix:

- If Telegram should stay disabled, keep it disabled and do not create the files.
- If Telegram should be enabled, create the files, write real values, and restrict them:

```bash
install -m 600 -o root -g root /dev/null /etc/larops/secrets/telegram_bot_token
install -m 600 -o root -g root /dev/null /etc/larops/secrets/telegram_chat_id
```

Then retry the alert/notify workflow.

## `create site` fails with missing source path

Symptom:

```text
Source path does not exist or is not a directory: /var/www/source/example.com
```

Meaning:

- You did not provide `--source`.
- `deploy.source_base_path/<domain>` does not exist yet.
- LarOps could not infer how to create the source.

Fix options:

Use an existing local source:

```bash
larops create site example.com --source /path/to/app --apply
```

Clone from Git:

```bash
larops create site example.com --git-url https://github.com/acme/example-app.git --apply
```

Bootstrap a Laravel skeleton:

```bash
larops create site example.com --profile small-vps --apply
```

## `create site` says metadata already exists

Symptom:

```text
Application already exists. Use --force to recreate metadata.
```

Meaning:

- A previous `create site` run already created `state/apps/<domain>.json`.
- Provisioning did not finish, so rerunning without `--force` is blocked.

Inspect first if needed:

```bash
larops --json app info example.com
```

Recovery:

```bash
larops create site example.com --force --apply
```

Use `--force` for recovery or deliberate recreation, not as the normal day-to-day path for a healthy app.

## Where is the real `.env` after `create site`?

Meaning:

- LarOps uses the shared file:
  - `/var/www/<domain>/shared/.env`
- The current release usually has:
  - `/var/www/<domain>/current/.env`
  - as a symlink to the shared file
- If `create site --with-db` succeeded, LarOps already synced the main `DB_*` variables into `shared/.env`

Use this rule:

- edit `shared/.env`
- do not edit old release directories
- do not confuse LarOps DB secret files with the Laravel `.env`

If the database was provisioned by LarOps and you want to audit the generated credentials, inspect:

- `.larops/state/secrets/db/<domain>.txt`
- `.larops/state/secrets/db/<domain>.cnf`

## `create site --with-db` fails because the database or user already exists

Symptom:

```text
Database already exists: example_com
```

Or:

```text
Database user already exists: example_com@127.0.0.1
```

Meaning:

- LarOps reached the DB bootstrap step.
- The target database or user already exists already.
- LarOps stops instead of silently reusing or mutating an existing DB.

Fix options:

- Skip `--with-db` and point the app at the existing DB intentionally.
- Or choose explicit names:

```bash
larops create site example.com --with-db --db-name appdb --db-user appuser --apply
```

- Or provision the DB first:

```bash
larops db provision example.com --database appdb --user appuser --apply
```

## `create site --with-db` fails with `mysql: command not found`

Symptom:

```text
mysql: command not found
```

Meaning:

- The host does not have the local MySQL/MariaDB client installed.
- This usually means the host was bootstrapped before the small-vps default changed, or you explicitly used `--no-data`.

Fix:

If you want a local DB on the same VPS:

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --with-db --force --apply
```

If you do not want a local DB on the VPS:

- remove `--with-db`
- provision/use an existing external database instead

## Laravel app fails because `vendor/autoload.php` is missing

Symptom:

```text
Failed opening required '.../vendor/autoload.php'
```

Meaning:

- The release source was deployed without PHP dependencies installed.
- Current LarOps now auto-runs `composer install` for releases that contain `composer.json` and are missing `vendor/autoload.php`.
- If you hit this on an older host build, update LarOps and make sure `composer` is installed in the web stack.

Fix:

```bash
larops stack install --web --apply
```

Then redeploy or recreate the site.

## Do I still need to run `php artisan key:generate` and `migrate` manually?

Usually no for a fresh `create site` run when the deployed source contains `artisan`.

Current LarOps behavior:

- auto-runs `composer install --no-scripts` when the release is missing `vendor/autoload.php`
- auto-runs Laravel bootstrap after deploy:
  - `key:generate` only when `APP_KEY` is missing
  - `package:discover --ansi`
  - `migrate --force`
  - `optimize:clear`
  - `optimize`
- auto-creates the standard shared Laravel runtime directories:
  - `storage/framework/cache/data`
  - `storage/framework/sessions`
  - `storage/framework/views`
  - `storage/logs`
  - `storage/app/public`
  - `bootstrap/cache`

Manual artisan commands are still useful when:

- you are repairing an older release created before this behavior existed
- you intentionally disabled or bypassed the normal create flow
- your app requires additional project-specific setup commands

## Laravel bootstrap fails with `Please provide a valid cache path`

Symptom:

```text
Please provide a valid cache path.
```

Meaning:

- Laravel reached the bootstrap phase, but the writable runtime tree under `storage/` or `bootstrap/cache` was missing or broken for that release.
- Current LarOps creates these runtime directories automatically during release preparation for sources that contain `artisan`.

Fix:

- Update LarOps on the host.
- Re-run `create site --force --apply` or `app deploy --apply` so a fresh release gets prepared with the runtime tree.
- Verify the shared runtime directories if you need to inspect manually:

```bash
ls -ld /var/www/<domain>/shared/storage/framework/views
ls -ld /var/www/<domain>/shared/storage/framework/cache/data
ls -ld /var/www/<domain>/shared/storage/framework/sessions
ls -ld /var/www/<domain>/shared/storage/logs
ls -ld /var/www/<domain>/shared/bootstrap/cache
```

## `composer install` fails because the lock file requires a newer PHP version

Symptom:

```text
Your lock file does not contain a compatible set of packages.
... requires php >=8.4
```

Meaning:

- The application `composer.lock` was generated for a newer PHP version than the host runtime.
- LarOps can pin the Debian-family web stack with `--php`, but it cannot make an incompatible lock file work on an older host.
- On Ubuntu and Debian, LarOps now prepares the matching external PHP package repository automatically when you pin a newer PHP version.
- The lock file still has to match the PHP version you actually deploy.

Fix:

1. Either regenerate `composer.lock` for the PHP version you actually deploy.
2. Or rebuild the host web stack with the matching PHP version and recreate or redeploy the site.

Example:

```bash
larops bootstrap init --php 8.4 --apply
larops create site example.com --php 8.4 --force --apply
```

## Laravel bootstrap fails with `could not find driver`

Symptom:

```text
could not find driver
```

Meaning:

- PHP is installed, but the runtime is missing the DB extension needed by the app during bootstrap, usually `pdo_mysql` or `pdo_pgsql`.
- Current LarOps includes the common PHP DB drivers in the `web` stack by default.

Fix:

- Update LarOps on the host.
- Re-run the web stack install with the same PHP version you plan to use:

```bash
larops stack install --web --php 8.4 --apply
```

- Then recreate or redeploy the site:

```bash
larops create site example.com --php 8.4 --force --apply
```

## `ssl issue` fails because `certbot` is missing

Symptom:

```text
certbot is not installed
```

Meaning:

- The host was bootstrapped with an older LarOps build before `certbot` was included in the default `web` stack, or `certbot` was removed manually.

Fix:

```bash
larops stack install --web --apply
larops ssl issue example.com --challenge http --apply
```

Or rerun host bootstrap:

```bash
larops bootstrap init --apply
```

## `create site` with `-le` fails during certificate issuance

Meaning:

- Site creation reached the Let's Encrypt step but Certbot or the HTTP challenge prerequisites are not ready.

Safer recovery path:

1. Create the site without `-le`.
2. Verify Nginx/public webroot manually.
3. Run `larops ssl issue <domain> --challenge http --apply`.
4. Enable auto-renew after success:

```bash
larops ssl auto-renew enable --apply
```
