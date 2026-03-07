# LarOps

CLI vận hành server theo hướng Laravel-first, dùng để bootstrap host Linux, deploy ứng dụng, quản lý runtime, backup/restore, hardening bảo mật và observability.

Ngôn ngữ tài liệu:

- Landing page: [README.md](README.md)
- English manual: [README.en.md](README.en.md)

## Mục lục

1. LarOps là gì
2. Khi nào nên dùng
3. Tính năng chính
4. Yêu cầu môi trường
5. Cài đặt nhanh
6. Khái niệm cốt lõi
7. Ý nghĩa thực tế của các lệnh cấp cao
8. Cấu hình
9. Luồng vận hành chuẩn
10. Preset site và runtime policy
11. Cheat sheet lệnh
12. Telegram, metrics và log shipping
13. Security automation
14. Docker, local QA và CI/CD
15. Ghi chú vận hành an toàn
16. Troubleshooting
17. Cấu trúc repo
18. Tài liệu liên quan

## LarOps là gì

LarOps được thiết kế cho các team muốn tốc độ thao tác kiểu WordOps, nhưng tập trung vào nhu cầu thật của ứng dụng Laravel:

- provision host Linux nhanh
- tạo site và deploy release có cấu trúc rõ ràng
- quản lý `worker`, `scheduler`, `horizon` nhất quán qua `systemd`
- backup/restore an toàn hơn, có `verify` và `restore-verify`
- tăng mức tự động hóa cho security baseline, hardening, monitoring và alerting

LarOps phù hợp nhất hiện tại cho mô hình:

- 1 host Linux chạy 1 hoặc nhiều app Laravel
- có `systemd`
- cần control plane bằng CLI thay vì viết shell script rời rạc

## Khi nào nên dùng

Dùng LarOps khi bạn muốn:

- chuẩn hóa thao tác vận hành Laravel trên VPS hoặc server riêng
- giảm số script thủ công cho deploy, rollback, backup và runtime
- có baseline bảo mật đủ tốt cho production single-node
- gom alert, health check, metrics export và log shipping vào một công cụ

Không nên xem LarOps là nền tảng HA/multi-node hoàn chỉnh. Phần mạnh nhất hiện tại là `serious single-node Laravel ops`.

## Tính năng chính

- Provisioning stack: `stack install`, `bootstrap init`
- App/site lifecycle: `app create`, `app deploy`, `app rollback`, `site create`, `site delete`, `site restore`
- Runtime management: `worker`, `scheduler`, `horizon`, `site runtime`, `reconcile`
- SSL lifecycle: `ssl issue`, `ssl renew`, `ssl auto-renew`
- Database ops: `db backup`, `db restore`, `db verify`, `db restore-verify`, `db offsite`
- Notifications: `notify telegram`, `alert set`, `alert test`
- Security baseline: `security install`, `security status`, `security posture`, `security report`
- Preventive hardening: `secure ssh`, `secure nginx`
- Monitor: `monitor scan`, `monitor fim`, `monitor service`, `monitor app`
- Health and observability: `doctor quick`, `doctor run`, `doctor fleet`, `doctor metrics`, `observability logs`

## Yêu cầu môi trường

Production host:

- Linux có `systemd`
- Python `>= 3.11`
- quyền `root` hoặc `sudo` cho các tác vụ hệ thống
- network outbound để cài package, gọi Let’s Encrypt, Telegram, object storage

Hệ điều hành khuyến nghị:

- Ubuntu 24.04 LTS
- Ubuntu 22.04 LTS
- Debian 12

Khuyến nghị cấu hình VPS thực dụng:

- Mức tối thiểu để lab / thử nghiệm:
  - 1 vCPU
  - 1 GB RAM
  - 20 GB SSD
  - chỉ phù hợp để test CLI, không phù hợp production Laravel nghiêm túc
- Mức tối thiểu cho small production:
  - 2 vCPU
  - 2 GB RAM
  - 40 GB SSD
  - phù hợp 1 app Laravel nhỏ, traffic thấp, queue nhẹ
- Mức khuyến nghị cho serious single-node Laravel host:
  - 4 vCPU
  - 4 đến 8 GB RAM
  - 80+ GB SSD
  - phù hợp chạy chung Nginx + PHP-FPM + MariaDB/Postgres + Redis + workers + monitoring trên một máy
- Nếu có workload queue nặng, Horizon, import/export lớn:
  - 4 đến 8 vCPU
  - 8+ GB RAM
  - SSD nhanh và đủ dư cho releases, logs, backups

Ghi chú vận hành:

- Nếu web, database, Redis và worker cùng chạy trên một VPS, RAM thường là nút thắt đầu tiên.
- Offsite backup, log shipping và metrics exporter cũng tiêu tốn tài nguyên nền.
- Với production single-node nghiêm túc, 2 GB RAM thường là mức “chạy được”, chưa phải mức “thoải mái”.

Development:

- Python 3.11+
- Docker nếu muốn chạy local QA trong container

## Cài đặt nhanh

Cài bản pin version:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

Bootstrap host và tạo site đầu tiên:

```bash
larops bootstrap init --apply
larops create site example.com --apply
```

Biến thể cho VPS yếu:

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --apply
```

Local development:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
larops --help
pytest -q
```

## Khái niệm cốt lõi

### 1. Plan trước, apply sau

Phần lớn lệnh LarOps chạy ở chế độ plan mặc định:

- không có `--apply`: chỉ preview
- có `--apply`: thực thi thay đổi

Đây là quy tắc nên giữ khi vận hành production.

### 2. State nằm trong `state_path`

LarOps lưu metadata ở `state_path`, ví dụ:

- `state/apps/<domain>.json`: app metadata
- `state/runtime/<domain>/...`: runtime specs
- `state/secrets/db/<domain>.cnf`: DB credentials
- `state/security/...`: monitor state, FIM baseline

### 3. Runtime là spec + unit

Các process như `worker`, `scheduler`, `horizon` được biểu diễn bởi:

- JSON spec để trace cấu hình
- `systemd` unit nếu `systemd.manage=true`

### 4. Deploy là release-based

Mỗi lần deploy tạo một release riêng, rồi switch `current` sang release mới. Nhờ vậy rollback rõ ràng hơn so với overwrite trực tiếp mã nguồn đang chạy.

## Ý nghĩa thực tế của các lệnh cấp cao

Phần này dùng để tránh hiểu nhầm. Một số lệnh của LarOps nghe khá giống nhau, nhưng phạm vi tác động khác nhau.

### `larops bootstrap init`

Lệnh này làm gì:

- Có thể cài các group package của host:
  - `web` = `nginx`, PHP-FPM và các extension PHP lõi
  - `data` = `mariadb-server`, `redis-server`
  - `postgres` = `postgresql`
  - `ops` = `fail2ban`, `ufw`
- Có thể ghi file config mặc định nếu bật `--write-config` và file đích chưa tồn tại.
- Nếu có `--domain`, nó còn có thể khởi tạo app metadata và deploy release đầu tiên từ `--source`.
- Có hỗ trợ `--profile small-vps` cho VPS yếu:
  - mặc định chỉ giữ `web + ops`
  - bỏ local `data` trừ khi bạn chủ động thêm `--data`
  - ghi runtime restart policy bảo thủ hơn vào file config sinh ra

Lệnh này không làm gì:

- Không thay thế hoàn toàn cho `site create`
- Không tự issue SSL certificate
- Không tự bật runtime processes trừ khi bạn bật ở bước sau
- Không tự tạo full flow vhost production hoàn chỉnh cho app Laravel

Hiểu ngắn gọn:

- `bootstrap init` = chuẩn bị host
- `site create` = flow tạo site theo góc nhìn ứng dụng
- Với VPS yếu, nên bắt đầu bằng `larops bootstrap init --profile small-vps --apply`

### `larops site create`

Lệnh này làm gì:

- Tạo metadata cho app/domain
- Có thể deploy source vào release layout
- Chạy deploy phases (`build`, `pre-activate`, `post-activate`, `verify`) nếu bật deploy
- Có thể enable runtime theo preset hoặc theo flag
- Có hỗ trợ `--profile small-vps` cho Laravel nhẹ hơn:
  - `type=laravel`
  - `cache=fastcgi`
  - `worker=false`
  - `scheduler=true`
  - `horizon=false`
- Có thể issue Let’s Encrypt nếu dùng `-le`
- Có hỗ trợ `--atomic` để rollback khi create flow fail

Lệnh này không làm gì:

- Không phải lệnh cài package toàn host
- Giả định host đã được chuẩn bị ở mức cơ bản, thường là sau bootstrap

### `larops app deploy`

Lệnh này làm gì:

- Tạo release mới từ source
- switch `current` sang release mới
- chạy deploy phases và health/verify checks nếu cấu hình bật
- ghi metadata và release manifest

Lệnh này không làm gì:

- Không bootstrap host
- Không phải flow tạo site lần đầu

### `larops security install`

Lệnh này làm gì:

- Áp baseline security ở mức host:
  - UFW allow SSH/HTTP/HTTPS
  - optional UFW limit SSH
  - Fail2ban jail/filter cho SSH và Nginx scan pattern phổ biến

Lệnh này không làm gì:

- Không harden policy của `sshd` beyond baseline firewall/jail
- Không harden Nginx config
- Đây là baseline security, chưa phải full host hardening

### `larops security posture`

Lệnh này làm gì:

- Tạo report hợp nhất cho:
  - baseline `ufw/fail2ban`
  - `secure ssh`
  - `secure nginx`
  - monitor timers
  - Telegram notifier
  - app monitor timers của các app đã đăng ký

Lệnh này không làm gì:

- Không apply thay đổi nào
- Đây là lệnh inspect/report, không phải remediation command

### `larops monitor scan run`

Lệnh này làm gì:

- Đọc incremental Nginx access log từ offset đã lưu
- Phát hiện probe như `/.env`, `/.git`, `wp-login.php`, path traversal và các path bẩn tương tự
- Đánh giá `threshold-hits` trong `window-seconds` rolling window

Lệnh này không làm gì:

- Không phải WAF
- Không tự block traffic; nó emit event và alert

### `larops site runtime enable|disable|reconcile|status`

Lệnh này làm gì:

- Quản lý runtime process cho site:
  - `worker`
  - `scheduler`
  - `horizon`
- Ghi runtime spec và, nếu bật, quản lý luôn `systemd` unit tương ứng.
- `reconcile` cố gắng kéo runtime về trạng thái mong muốn nhưng vẫn tôn trọng restart policy.

Lệnh này không làm gì:

- Không deploy code ứng dụng
- Không thay thế `app deploy`

### `larops db offsite status`

Lệnh này làm gì:

- Kiểm tra artifact backup mã hóa đang nằm trên object storage
- Báo freshness và phát hiện upload remote bị incomplete

Lệnh này không làm gì:

- Không tạo backup mới
- Không verify ngữ nghĩa dữ liệu trong DB; đây là kiểm tra phía storage

### `larops db offsite restore-verify`

Lệnh này làm gì:

- Tải artifact backup mã hóa từ object storage
- Validate checksum và HMAC
- restore vào DB tạm để xác nhận artifact có thể restore được thật

Lệnh này không làm gì:

- Không chứng minh dữ liệu nghiệp vụ bên trong là đúng
- Nó xác nhận khả năng recover, không xác nhận business correctness

### `larops observability logs enable`

Lệnh này làm gì:

- Cấu hình và quản lý hook log shipping bằng Vector
- Ship log LarOps, log Laravel và log Nginx tới sink đã cấu hình

Lệnh này không làm gì:

- Không tự cung cấp log backend
- Bạn vẫn cần nơi nhận log thật, ví dụ Vector upstream hoặc HTTP ingestion endpoint

### `larops doctor metrics run`

Lệnh này làm gì:

- Chuyển health từ `doctor fleet` thành Prometheus textfile metrics
- Cho phép nối LarOps health vào `node_exporter` textfile collector

Lệnh này không làm gì:

- Không phải monitoring platform hoàn chỉnh
- Nó export health signal, không thay thế Prometheus/Grafana hay hệ alert riêng

### `larops doctor fleet`

Lệnh này làm gì:

- Tổng hợp health của host và toàn bộ app đã đăng ký
- Giúp operator xem runtime, backup, timers và app health ở một chỗ

Lệnh này không làm gì:

- Không phải application tracing sâu
- Phụ thuộc vào những checks và telemetry mà LarOps thu được trên máy

## Cấu hình

Config mặc định:

- `/etc/larops/larops.yaml`

Override config file:

```bash
larops --config /path/to/larops.yaml ...
```

Một số nhóm cấu hình quan trọng:

- `deploy`: đường dẫn release, health check, verify phase, migrate phase, shared dirs/files
- `systemd`: có quản lý unit hay không, unit dir, user chạy service
- `runtime_policy`: giới hạn restart và cooldown cho `worker`, `scheduler`, `horizon`
- `events`: event stream JSONL
- `notifications.telegram`: token, chat id, mức severity tối thiểu
- `backups`: encryption, offsite storage, retention
- `doctor`: app probes, heartbeat, queue backlog, failed jobs

Nguyên tắc nên theo:

- ưu tiên secret file hơn inline secret trong YAML
- fail-fast nếu secret file thiếu hoặc rỗng
- pin version installer trong production

## Luồng vận hành chuẩn

### 1. Bootstrap host

```bash
larops bootstrap init --apply
```

Bootstrap một lần kèm site đầu tiên:

```bash
larops bootstrap init --domain example.com --source /var/www/source/example.com --apply
```

### 2. Tạo site hoặc app

```bash
larops create site example.com --apply
larops site create example.com --apply
```

Theo preset Laravel + Redis:

```bash
larops site create example.com --type laravel --cache redis --php 8.3 --apply
```

Kèm Let’s Encrypt:

```bash
larops site create example.com -le --le-email ops@example.com --apply
```

### 3. Deploy và rollback

```bash
larops app deploy example.com --source /var/www/source/example.com --apply
larops app rollback example.com --to previous --apply
larops --json app info example.com
```

LarOps hỗ trợ:

- health gate
- verify phase
- rollback khi health/verify fail nếu cấu hình bật
- refresh runtime sau deploy tùy strategy

### 4. Bật runtime

```bash
larops site runtime enable example.com -w -s -a
larops site runtime status example.com
larops site runtime reconcile example.com -w -a
```

Direct commands vẫn dùng được:

```bash
larops worker enable example.com --queue default --concurrency 2 --apply
larops scheduler enable example.com --apply
larops horizon enable example.com --apply
```

### 5. Permission

```bash
larops site permissions example.com --apply
larops site permissions example.com --owner www-data --group www-data --apply
```

### 6. SSL lifecycle

```bash
larops ssl issue example.com --challenge http --apply
larops ssl auto-renew enable --apply
larops ssl auto-renew status
larops ssl renew --apply
larops ssl check example.com
```

### 7. Database backup, restore, offsite

MySQL/MariaDB ví dụ:

```bash
export LAROPS_DB_PASSWORD="strong-password"
larops db credential set example.com --user appuser --apply
larops db backup example.com --database appdb --retain-count 10 --apply
larops db status example.com
larops db verify --backup-file /path/backup.sql.gz
larops db restore-verify example.com --backup-file /path/backup.sql.gz --database appdb --apply
larops db offsite status example.com
larops db offsite restore-verify example.com --database appdb --apply
larops db auto-backup enable example.com --database appdb --apply
larops db list-backups example.com
larops db restore example.com --backup-file /path/backup.sql.gz --database appdb --apply
```

PostgreSQL ví dụ:

```bash
export LAROPS_DB_PASSWORD="strong-password"
larops db credential set example.com --engine postgres --user appuser --apply
larops db backup example.com --engine postgres --database appdb --apply
larops db restore-verify example.com --engine postgres --backup-file /path/backup.sql.gz --database appdb --apply
```

Ghi nhớ:

- backup chỉ có ý nghĩa khi `restore-verify` pass
- offsite backup hiện dùng backend `s3` tương thích, nên dùng được với S3, R2, MinIO
- backup offsite có encryption client-side và HMAC để phát hiện artifact bị sửa

### 8. Health, metrics, log shipping

```bash
larops doctor quick
larops --json doctor run example.com
larops --json doctor fleet
larops doctor metrics run --output-file /var/lib/node_exporter/textfile_collector/larops.prom --apply
larops doctor metrics timer enable --output-file /var/lib/node_exporter/textfile_collector/larops.prom --apply
larops observability logs enable --sink vector --vector-address 10.0.0.10:6000 --apply
larops observability logs status
```

### 9. Xóa site an toàn

```bash
larops site delete example.com --purge --confirm example.com --apply
larops site restore example.com --checkpoint-file /path/checkpoint.tar.gz --apply
```

## Preset site và runtime policy

Preset `site create`:

- `--profile small-vps`: preset Laravel nhẹ cho VPS yếu:
  - `type=laravel`
  - `cache=fastcgi`
  - `worker=false`
  - `scheduler=true`
  - `horizon=false`
  - flag explicit vẫn override được (`--worker`, `--cache redis`, `--no-scheduler`, ...)

- `--type php`
- `--type mysql`
- `--type laravel`
- `--type queue`
- `--type horizon`

Preset cache:

- `--cache none`
- `--cache fastcgi`
- `--cache redis`
- `--cache supercache`

Runtime policy cho `worker`, `scheduler`, `horizon` gồm:

- `max_restarts`
- `window_seconds`
- `cooldown_seconds`
- `auto_heal`

Ý nghĩa thực tế:

- restart tay hay reconcile đều bị chặn nếu vượt policy
- tránh loop restart vô hạn khi process lỗi liên tục

## Cheat sheet lệnh

Xem phần này như index thao tác nhanh. Ví dụ đầy đủ đã nằm ở [Luồng vận hành chuẩn](#luồng-vận-hành-chuẩn).

- Stack và bootstrap:
  - `larops stack install --web --data --ops --apply`
  - `larops bootstrap init --apply`
- App lifecycle:
  - `larops app create example.com --apply`
  - `larops app deploy example.com --source /var/www/source/example.com --apply`
  - `larops app rollback example.com --to previous --apply`
  - `larops --json app info example.com`
- Site lifecycle:
  - `larops site create example.com --apply`
  - `larops site runtime enable example.com -w -s -a`
  - `larops site runtime reconcile example.com -w -a`
  - `larops site permissions example.com --apply`
  - `larops site delete example.com --purge --confirm example.com --apply`
- Runtime direct:
  - `larops worker status example.com`
  - `larops scheduler run-once example.com --apply`
  - `larops horizon terminate example.com --apply`
- SSL:
  - `larops ssl auto-renew enable --apply`
  - `larops ssl auto-renew disable --apply`
  - `larops ssl auto-renew status`
- Database:
  - `larops db credential show example.com`
  - `larops db backup example.com --database appdb --apply`
  - `larops db status example.com`
  - `larops db offsite status example.com`
  - `larops db verify --backup-file /path/backup.sql.gz`
  - `larops db offsite restore-verify example.com --database appdb --apply`
  - `larops db auto-backup enable example.com --database appdb --apply`
- Notification:
  - `larops notify telegram daemon enable --apply`
  - `larops notify telegram daemon status`
  - `larops alert set --telegram-token "<token>" --telegram-chat-id "<chat-id>" --apply`
  - `larops alert test --apply`
- Security:
  - `larops security install --apply`
  - `larops security status`
  - `larops security posture`
  - `larops security report --since 24h`
  - `larops secure ssh --ssh-key-only --allow-user deploy --allow-group wheel --max-startups 10:30:60 --apply`
  - `larops secure nginx --profile strict --block-path /private/ --server-config-file /etc/nginx/sites-enabled/example.conf --apply`
- Monitor:
  - `larops monitor scan run --threshold-hits 8 --window-seconds 300 --apply`
  - `larops monitor fim init --root /var/www/example.com/current --apply`
  - `larops monitor service run --profile laravel-host --apply`
  - `larops monitor app run example.com --apply`
  - `larops monitor scan timer enable --on-calendar "*-*-* *:*:00" --apply`
  - `larops monitor app timer enable example.com --on-calendar "*-*-* *:*:00" --apply`

Ghi chú vận hành:

- Gợi ý VPS nhỏ: scan `*-*-* *:0/2:00`, FIM `*-*-* *:0/30:00`, service watchdog `*-*-* *:*:00`
- Gợi ý traffic cao: scan `*-*-* *:*:00`, FIM `*-*-* *:0/10:00`, service watchdog `*-*-* *:*:00`
- `monitor scan` đánh giá `threshold-hits` trong `window-seconds` rolling window, không phải chỉ trong một lần chạy
- Built-in profile cho service watchdog:
  - `laravel-host` = `nginx`, `php-fpm`, `mariadb`, `redis`
  - `laravel-postgres-host` = `nginx`, `php-fpm`, `postgresql`, `redis`

## Telegram, metrics và log shipping

Telegram secrets:

```bash
sudo install -d -m 700 /etc/larops/secrets
echo "123456:BOT_TOKEN" | sudo tee /etc/larops/secrets/telegram_bot_token >/dev/null
echo "-1001234567890" | sudo tee /etc/larops/secrets/telegram_chat_id >/dev/null
sudo chmod 600 /etc/larops/secrets/telegram_bot_token /etc/larops/secrets/telegram_chat_id
```

Bật notifier daemon:

```bash
larops notify telegram daemon enable --apply
larops notify telegram daemon status
larops alert test --apply
```

Metrics exporter:

```bash
larops doctor metrics timer enable \
  --output-file /var/lib/node_exporter/textfile_collector/larops.prom \
  --apply
```

Vector log shipping:

```bash
larops observability logs enable \
  --sink vector \
  --vector-address 10.0.0.10:6000 \
  --apply
```

## Security automation

LarOps hiện có 3 lớp chính:

### 1. Baseline

- `security install`: UFW + Fail2ban baseline
- `security status`: kiểm tra UFW, Fail2ban, jail/filter files
- `security report`: tổng hợp ban IP và scan log theo time window thật

### 2. Preventive hardening

- `secure ssh`: harden `sshd_config.d`
- `secure nginx`: generate hardening config/snippet cho Nginx

### 3. Detection và response

- `monitor scan`: phát hiện probe vào path bẩn, alert theo rolling window
- `monitor fim`: phát hiện đổi file nhạy cảm
- `monitor service`: watchdog cho `mariadb`, `redis`, `nginx`, `php-fpm`...
- `monitor app`: heartbeat, queue backlog, failed jobs, app checks
- `security posture`: một report hợp nhất để xem tổng thể security automation đã vào đúng trạng thái chưa

## Docker, local QA và CI/CD

Docker/local QA:

```bash
docker compose build
docker compose run --rm larops-test
docker compose run --rm larops-cli
```

DB integration test:

```bash
LAROPS_RUN_DB_INTEGRATION=1 \
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD=rootpass \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_USER=postgres POSTGRES_PASSWORD=postgrespass \
pytest -q tests/integration/test_db_engine_integration.py
```

CI hiện bao gồm:

- Ruff + Pytest trên nhiều phiên bản Python
- DB integration tests với MySQL/Postgres thật
- Docker build + CLI smoke

### GitHub CI/CD

Workflow chính:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

`ci.yml` chạy cho:

- pull request vào `main`
- push vào `main`
- manual dispatch

`release.yml` chạy khi push tag dạng `vX.Y.Z`.

### Release process

Release flow:

```bash
scripts/release.sh <version>
git push origin main
git push origin v<version>
```

Script release sẽ:

- bump version trong `pyproject.toml` và `src/larops/__init__.py`
- cập nhật `CHANGELOG.md`
- tạo release commit và annotated tag

## Ghi chú vận hành an toàn

- luôn ưu tiên `plan` rồi mới `--apply`
- dùng secret file thay vì inline secret
- đặt permission secret về `0600`
- với deploy production, dùng bản installer pin version
- với backup production, coi backup là chưa xong nếu chưa có `restore-verify`
- với security, coi posture là chưa đủ nếu hardening, timer và notifier chưa xanh

## Troubleshooting

### `Application is not registered`

Nguyên nhân:

- thiếu `state/apps/<domain>.json`

Cách xử lý:

```bash
larops site create <domain> --apply
```

### Runtime enable báo thiếu release hiện tại

Nguyên nhân:

- chưa có `current` symlink hợp lệ

Cách xử lý:

```bash
larops app deploy <domain> --source <path> --apply
```

### Lỗi Telegram secret file

Nguyên nhân:

- file token/chat id thiếu hoặc rỗng

Cách xử lý:

- tạo file secret
- ghi giá trị thật
- `chmod 600`
- chạy lại lệnh

### SSL auto-renew timer tồn tại nhưng không active

Cách xử lý:

```bash
larops ssl auto-renew status
larops ssl auto-renew enable --apply
```

### DB credential file sai permission

Cách xử lý:

```bash
chmod 600 /var/lib/larops/state/secrets/db/<domain>.cnf
larops db backup <domain> --database <db> --apply
```

## Cấu trúc repo

```text
src/larops/
  commands/        nhóm lệnh CLI
  services/        business logic và integration
  core/            shell, locks, events
config/            sample config
scripts/           installer và release helpers
docs/              runbook, review notes
tests/             unit/integration tests
```

## Tài liệu liên quan

- Landing page song ngữ: [README.md](README.md)
- English manual: [README.en.md](README.en.md)
- Production runbook: [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)

## Production Runbook

Runbook production chi tiết nằm ở:

- [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)

Nên dùng runbook này khi bạn cần:

- checklist bootstrap host production
- lịch timer cho monitor/backup/ssl
- lệnh security baseline và hardening
- quy trình review health, backup và observability
