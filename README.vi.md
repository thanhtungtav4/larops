# LarOps

CLI vận hành server theo hướng Laravel-first, dùng để bootstrap host Linux, deploy ứng dụng, quản lý runtime, backup/restore, hardening bảo mật và observability.

Ngôn ngữ tài liệu:

- Landing page: [README.md](README.md)
- English manual: [README.en.md](README.en.md)
- Command index: [docs/COMMANDS.md](docs/COMMANDS.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

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
- App/site lifecycle: `app create`, `app deploy`, `app refresh`, `app rollback`, `app bootstrap`, `app info`, `site create`, `site delete`, `site restore`
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

Hệ điều hành được support cho production:

- Ubuntu 24.04 LTS
- Ubuntu 22.04 LTS
- Debian 12

Các target dạng preview / evaluation:

- Debian 13
- Rocky Linux 9
- AlmaLinux 9
- RHEL 9

Ma trận support chi tiết:

- [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)

Phạm vi thực dụng:

- Nếu bạn muốn đường production ít rủi ro nhất, hãy giữ Ubuntu 22.04/24.04 hoặc Debian 12.
- Debian 13 và EL9-family hiện phù hợp để test, đánh giá, chuẩn bị rollout sau này hơn là xem như production target chính thức.

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

Cài nhanh:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo bash
```

Bootstrap host và tạo site đầu tiên:

```bash
larops bootstrap init --apply
larops create site example.com --apply
```

Cài bản pin version sau khi GitHub release đã được publish:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | \
  sudo LAROPS_VERSION=0.1.0 bash
```

Nếu release mặc định chưa được publish, installer sẽ tự fallback sang snapshot mới nhất từ `main` để bootstrap ban đầu.

Biến thể cho VPS yếu:

```bash
larops bootstrap init --profile small-vps --apply
larops create site example.com --profile small-vps --apply
```

Pin web stack sang PHP version khác trên host Debian-family:

```bash
larops bootstrap init --php 8.4 --apply
larops create site example.com --php 8.4 --apply
```

Khi pin PHP version khác mặc định trên Ubuntu hoặc Debian, LarOps sẽ tự chuẩn bị PHP package repository tương ứng trước khi cài `php<major.minor>-*`.

Lấy source từ Git và tạo DB luôn:

```bash
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --apply
```

`create site` sẽ xử lý source như sau trên host mới:

- Nếu `deploy.source_base_path/<domain>` đã tồn tại, LarOps deploy từ source local đó.
- Nếu source còn thiếu và có `--git-url`, LarOps sẽ clone repo vào `deploy.source_base_path/<domain>` trước.
- Nếu source còn thiếu và site hiệu lực thuộc họ Laravel, LarOps sẽ tự bootstrap source bằng `composer create-project laravel/laravel`.
- Nếu release có `composer.json` nhưng chưa có `vendor/autoload.php`, LarOps sẽ tự chạy `composer install` ở build phase.
- Nếu release có `package.json`, có `vite.config.*`, và chưa có `public/build/manifest.json`, LarOps sẽ tự chạy `npm ci|install` và `npm run build` ở build phase.
- Đường auto-build frontend mặc định hiện giả định app dùng npm và sẽ preflight `package.json -> engines.node` với Node runtime đang có trên host.
- LarOps cũng preflight sớm các build tool bắt buộc trước khi vào build phase:
  - `deploy.composer_binary` phải tồn tại nếu build có bước Composer install
  - `npm` và `node` phải tồn tại nếu dùng đường auto-build Vite mặc định
- Composer ở build phase chạy với `--no-scripts`; phần Laravel package discovery được dời sang bootstrap app sau khi `.env` và symlink release đã sẵn sàng.
- Nên dùng cùng giá trị `--php` ở bước bootstrap host và create site để Nginx/FPM khớp đúng runtime đã cài.
- Trên Ubuntu và Debian, LarOps sẽ tự chuẩn bị external PHP repository tương ứng khi version pin mới hơn mặc định của distro.
- Nếu có `--with-db`, LarOps sẽ provision database/user của ứng dụng và ghi credential/password file trước khi deploy.
- Khi source được deploy có file `artisan`, `create site` mặc định dùng `--app-bootstrap-mode auto`:
  - tự ghi `APP_KEY` trực tiếp vào `shared/.env` nếu còn thiếu
  - chỉ chạy `migrate`, `package:discover`, và `optimize*` khi database của app có vẻ đã có schema
  - nếu LarOps không xác định được DB context an toàn, nó sẽ bỏ bootstrap Laravel thay vì ép chạy artisan ở lần create đầu
  - dùng `--app-bootstrap-mode eager` cho app bạn biết chắc boot an toàn ngay từ lần create đầu
  - dùng `--app-bootstrap-mode skip` nếu provider boot phụ thuộc schema hoặc dữ liệu seed ban đầu
- Nếu lần create đầu cố ý skip bootstrap Laravel, dùng `larops app bootstrap <domain> --apply` sau khi DB/schema đã sẵn sàng. Thêm `--seed` hoặc `--seeder-class <ClassName>` khi app cần seed dữ liệu trước khi boot bình thường.
- Với app đang lấy code từ git và đã tồn tại sẵn, `larops app refresh <domain> --seed --apply` là đường ngắn nhất để pull code mới, deploy release mới, rồi bootstrap lại app.
- Sau khi provision Nginx managed site, `create site` sẽ in smoke result nhẹ như `smoke http: 301` và `smoke https: 200` để bạn thấy ngay trạng thái HTTP/HTTPS thực tế.
- `bootstrap init --profile small-vps` giờ có local `data` stack theo mặc định. Chỉ dùng `--no-data` nếu bạn cố ý để database ở máy khác.
- Nếu lần create trước đã ghi `state/apps/<domain>.json` nhưng chưa hoàn tất, hãy chạy lại với `--force`.

Ví dụ create site thực dụng:

```bash
# source local
larops create site example.com --source /var/www/source/example.com --apply

# clone từ git và provision DB
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --apply

# app biết chắc boot an toàn ngay lần create đầu
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --app-bootstrap-mode eager \
  --apply

# provider boot phụ thuộc schema hoặc seed data
larops create site example.com \
  --git-url https://github.com/acme/example-app.git \
  --with-db \
  --app-bootstrap-mode skip \
  --apply

# bootstrap và seed release hiện tại ở bước sau
larops app bootstrap example.com \
  --seed \
  --seeder-class DemoSeeder \
  --apply
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

Mặc định, nếu `state_path` hoặc `events.path` là đường dẫn tương đối thì LarOps sẽ resolve theo thư mục cài đặt LarOps, không theo thư mục hiện tại của shell. Bạn có thể chạy `larops` từ `~`, `/opt/larops`, hay chỗ khác mà vẫn dùng cùng một state tree.

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
  - `web` = `nginx`, `certbot`, PHP-FPM và các extension PHP lõi
  - `data` = `mariadb-server`, `redis-server`
  - `postgres` = `postgresql`
  - `ops` = `fail2ban` và firewall backend của host (`ufw` trên Debian/Ubuntu, `firewalld` trên EL9)
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
- Không thay thế cho bài toán ingress/reverse proxy/CDN nhiều node ở mức rộng hơn

Hiểu ngắn gọn:

- `bootstrap init` = chuẩn bị host
- `site create` = flow tạo site theo góc nhìn ứng dụng
- Với VPS yếu, nên bắt đầu bằng `larops bootstrap init --profile small-vps --apply`
- File config sinh ra có thể chứa sẵn secret-file path cho feature đang tắt; các file đó chỉ bắt buộc khi feature tương ứng được bật.

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
- Trên host single-node được support, khi có deploy thì LarOps sẽ tự provision managed Nginx site config.
- Resolve source theo thứ tự:
  - dùng `--source` nếu có
  - nếu không thì dùng `deploy.source_base_path/<domain>`
  - nếu path này chưa có và có `--git-url`, clone vào đó
  - nếu path này chưa có và site hiệu lực thuộc họ Laravel, bootstrap bằng `composer create-project`
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
  - firewall allow SSH/HTTP/HTTPS (`ufw` trên Debian/Ubuntu, `firewalld` trên EL9)
  - optional SSH rate limit trên host dùng UFW
  - Fail2ban jail/filter cho SSH và Nginx scan pattern phổ biến

Lệnh này không làm gì:

- Không harden policy của `sshd` beyond baseline firewall/jail
- Không harden Nginx config
- Đây là baseline security, chưa phải full host hardening
- EL9 hiện vẫn ở mức preview, và một số distro có thể vẫn cần chuẩn bị repo Fail2ban thủ công

### `larops security posture`

Lệnh này làm gì:

- Tạo report hợp nhất cho:
  - baseline `firewall/fail2ban`
  - `secure ssh`
  - `secure nginx`
  - monitor timers
  - Telegram notifier
  - app monitor timers của các app đã đăng ký

Lệnh này không làm gì:

- Không apply thay đổi nào
- Đây là lệnh inspect/report, không phải remediation command
- Trên Debian-family, LarOps không thể tự chứng minh snippet hardening của Nginx đang active nếu bạn không truyền `--nginx-server-config-file`
- Trên EL9, LarOps có thể verify đường `default.d/*.conf` qua `nginx.conf` nếu bạn giữ default path hoặc truyền `--nginx-root-config-file`
- Nếu LarOps chỉ thấy các file hardening do nó quản lý nhưng chưa verify được hardening đang active, report sẽ xuống `warn` thay vì báo Nginx đã được harden
- Khi SELinux đang active, `secure ssh` và `secure nginx` sẽ tự chạy `restorecon -F` cho các file do LarOps quản lý trước khi validate/reload; nếu host thiếu `restorecon`, lệnh sẽ fail rõ.

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
- secret-file path có thể tồn tại sẵn trong config khi feature đang tắt; LarOps chỉ đọc khi feature tương ứng được bật hoặc bị override rõ ràng
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

Resolve source mặc định:

- Nếu có `--source`, LarOps dùng đúng path đó.
- Nếu không có `--source`, LarOps tìm `deploy.source_base_path/<domain>`.
- Nếu path này chưa tồn tại và có `--git-url`, LarOps clone repo vào đó rồi mới deploy.
- Nếu path này chưa tồn tại và site hiệu lực thuộc họ Laravel, LarOps tự bootstrap source bằng `composer create-project laravel/laravel`.
- Nếu có `--with-db`, LarOps provision database/user của ứng dụng và ghi credential/password file trước khi deploy.
- Nếu path này chưa tồn tại cho một site không thuộc họ Laravel và không có `--git-url`, lệnh sẽ fail và yêu cầu bạn cung cấp `--source` hoặc `--git-url`.

Deploy trực tiếp từ Git:

```bash
larops create site example.com --git-url https://github.com/acme/example-app.git --apply
```

Tạo skeleton Laravel mới trên VPS yếu:

```bash
larops create site example.com --profile small-vps --apply
```

Hành vi Nginx mặc định:

- Khi có deploy, `create site` sẽ tự tạo managed Nginx site config theo mặc định.
- Không có `-le`: vhost HTTP được tạo để site mở được ngay.
- Nếu domain đã có certificate hợp lệ sẵn, LarOps sẽ bind HTTPS mà không cần issue lại cert mới.
- Có `-le`: LarOps tạo vhost HTTP trước, issue cert, rồi rewrite sang HTTPS.
- Dùng `--no-nginx` nếu bạn chủ động quản lý ingress bên ngoài LarOps.

Sửa biến môi trường ứng dụng ở đâu sau khi create:

- Hãy sửa file shared `.env`, không sửa source tree của release:
  - Khi `create site --with-db` thành công, LarOps tự sync `DB_CONNECTION`, `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` vào file này.
  - `/var/www/<domain>/shared/.env`
- Release hiện tại thường nhìn thấy:
  - `/var/www/<domain>/current/.env`
  - dưới dạng symlink trỏ về `shared/.env`
- Không dùng `.larops/state/secrets/db/<domain>.cnf` hay `.txt` như app `.env`. Đây vẫn là secret/audit file do LarOps quản lý.

Sau khi sửa `.env`, flow thường dùng là:

```bash
cd /var/www/example.com/current
php artisan key:generate --force
php artisan migrate --force
php artisan optimize:clear
php artisan optimize
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

Nếu bạn dùng `create site --with-db`, LarOps sẽ in và lưu:

- tên database
- tên user
- file credential DB
- file password DB

LarOps đã tự sync các giá trị DB đó vào `/var/www/<domain>/shared/.env`. Bạn chỉ cần bổ sung các biến riêng của ứng dụng.

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

- `security install`: firewall + Fail2ban baseline
- `security status`: kiểm tra firewall backend hiện tại, Fail2ban, jail/filter files
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

- Telegram thực sự đã được bật, hoặc override rõ ràng khiến LarOps phải đọc file token/chat id, nhưng file đang thiếu hoặc rỗng

Cách xử lý:

- Nếu Telegram vẫn đang tắt, cứ để tắt và không cần tạo các file secret này.
- Nếu muốn bật Telegram, hãy:
  - tạo file secret
  - ghi giá trị thật
  - `chmod 600`
  - chạy lại lệnh

### `create site` báo thiếu source path

Nguyên nhân:

- Bạn không truyền `--source`, thư mục `deploy.source_base_path/<domain>` chưa tồn tại, và LarOps không suy ra được cách tạo source.

Cách xử lý:

```bash
larops create site <domain> --source /path/to/app --apply
```

hoặc:

```bash
larops create site <domain> --git-url https://github.com/org/repo.git --apply
```

hoặc với site/profile thuộc họ Laravel:

```bash
larops create site <domain> --profile small-vps --apply
```

### `create site` báo `Application already exists. Use --force to recreate metadata.`

Nguyên nhân:

- Lần `create site` trước đã tạo `state/apps/<domain>.json` nhưng provisioning chưa hoàn tất.

Cách xử lý:

```bash
larops --json app info <domain>
larops create site <domain> --force --apply
```

Chỉ dùng `--force` cho recovery hoặc recreate có chủ đích, không dùng tùy tiện trên app production đang khỏe.

### `larops` không chạy được sau khi cài

Nguyên nhân:

- Host còn một bản cài cũ từ trước khi installer được sửa phần virtualenv relocation.

Cách xử lý:

```bash
curl -fsSL https://raw.githubusercontent.com/thanhtungtav4/larops/main/scripts/install.sh | sudo bash
```

hoặc rebuild lại `/opt/larops/.venv` rồi cập nhật `/usr/local/bin/larops`.

### `ssl issue` báo thiếu `certbot`

Nguyên nhân:

- Host được bootstrap từ bản LarOps cũ trước khi `certbot` được đưa vào web stack mặc định, hoặc `certbot` đã bị gỡ thủ công.

Cách xử lý:

```bash
larops stack install --web --apply
larops ssl issue <domain> --challenge http --apply
```

Hoặc chạy lại bootstrap host:

```bash
larops bootstrap init --apply
```

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
- OS support matrix: [docs/OS_SUPPORT_MATRIX.md](docs/OS_SUPPORT_MATRIX.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)

## Production Runbook

Runbook production chi tiết nằm ở:

- [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)

Nên dùng runbook này khi bạn cần:

- checklist bootstrap host production
- lịch timer cho monitor/backup/ssl
- lệnh security baseline và hardening
- quy trình review health, backup và observability
