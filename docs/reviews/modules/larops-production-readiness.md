# LarOps Production Readiness Review

Ngày review: 2026-03-06

🚨 CẢNH BÁO NGHIÊM TRỌNG

LarOps hiện có nền CLI, test, runbook, security baseline và runtime control khá tốt cho môi trường lab/staging hoặc VPS đơn có operator biết rõ giới hạn của tool. Tuy nhiên, phần quan trọng nhất của production readiness cho Laravel vẫn còn hở ở đúng các điểm dễ gây downtime và lỗi âm thầm:

- Deploy/rollback mới dừng ở mức copy source + switch symlink, chưa phải release model an toàn cho Laravel production ([app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:188), [app_lifecycle.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/app_lifecycle.py:62)).
- Scheduler được chạy bằng vòng lặp `while true; do schedule:run; sleep 60; done`, có nguy cơ trễ hoặc bỏ lịch khi command chạy lâu ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:181)).
- Runtime worker concurrency > 1 đang được nhồi nhiều process vào một systemd unit shell, làm giảm năng lực xử lý âm thầm khi một child chết và không có recycle limit cho worker ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:170)).
- Backup tồn tại ở mức command, nhưng chưa có retention, freshness, verify, restore drill hay timer orchestration của chính LarOps ([db.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/db.py:166), [db_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/db_service.py:163)).
- Alert pipeline Telegram đọc `events.jsonl` theo offset đơn thuần, không theo inode/truncate nên có thể im lặng sau log rotation ([telegram_adapter.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/telegram_adapter.py:100)).

`ruff check` hiện pass và `pytest -q` hiện pass `108 passed, 2 skipped`, nhưng mức sẵn sàng production của tool vẫn bị chặn bởi các khoảng hở kiến trúc ở trên.

# 1. Tóm tắt điều hành

- Mức độ sẵn sàng production: `Thấp đến trung bình`
- Phù hợp hiện tại: `lab`, `staging`, `single VPS production có nhiều thao tác bù thủ công`
- Chưa phù hợp hiện tại: `serious production`, `multi-node`, `deploy yêu cầu rollback đáng tin cậy`, `scheduler/job business-critical`

Các rủi ro nghiêm trọng nhất:

- Release model chưa an toàn cho Laravel thực chiến: chưa có hook build/install/migrate/cache/smoke/restart runtime.
- Scheduler và worker runtime có hành vi dễ tạo lỗi âm thầm: miss schedule, degrade capacity, memory leak.
- Backup/alert/restore readiness chưa đủ để tự tin khi gặp sự cố thật.

Đánh giá tổng thể LarOps hiện tại:

- Mạnh ở CLI UX, `plan -> apply`, lock command, test coverage CLI, DB integration test, runbook và baseline hardening bằng `ufw/fail2ban/FIM/scan/Telegram`.
- Yếu ở chỗ LarOps hiện mới là “ops helper” tốt, chưa phải “production control plane” đáng tin cậy cho Laravel.

# 2. Architect Review

## 2.1 Host Bootstrap & Infrastructure Assumptions

Đánh giá: `Trung bình`

Vì sao:

- Repo assume mạnh vào `Ubuntu/Linux + systemd + root/sudo`, điều này rõ ràng và nhất quán trong docs và code ([README.md](/Volumes/Manager%20Data/Tool/larops/README.md:53), [bootstrap.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/bootstrap.py:62)).
- `stack install` và `bootstrap init` đang assume single-host: web, data, ops cùng host; không có abstraction tách web/app/db/cache/queue ([stack_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/stack_service.py:9)).
- Stack provisioning mới chỉ cài package, chưa cấu hình nginx/php-fpm/mariadb/redis thành production host thật sự ([stack_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/stack_service.py:34)).
- Bootstrap cài `supervisor` nhưng runtime thật dùng `systemd`; bootstrap cũng không provision PostgreSQL dù DB tooling hỗ trợ `postgres` backup/restore ([stack_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/stack_service.py:19)).

## 2.2 Deploy & Release Model

Đánh giá: `Kém`

Vì sao:

- `app deploy` chỉ load metadata, `copytree`, switch symlink, prune releases, update metadata ([app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:188), [app_lifecycle.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/app_lifecycle.py:68)).
- Không có các bước production-critical cho Laravel:
  - `composer install --no-dev`
  - asset build/release
  - `artisan migrate --force`
  - `config:cache`, `route:cache`, `view:cache`
  - `queue:restart`
  - smoke test sau deploy
- `health_check_path` được cấu hình nhưng không được dùng làm gate hay smoke test ở deploy/rollback ([config.py](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py:18)).
- `rollback` chỉ switch symlink và ghi metadata, không có runtime restart hay migration compatibility strategy ([app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:278)).
- `site create --atomic` chỉ bọc flow create-site, không giải quyết deploy thường ngày hoặc rollback production ([create.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/create.py:717)).

## 2.3 Runtime Process Management

Đánh giá: `Kém`

Vì sao:

- Có nền runtime tốt: spec JSON, systemd unit, restart policy, cooldown, auto-heal ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:249)).
- Nhưng implementation scheduler hiện tại không đúng nhu cầu production Laravel: `while true; do php artisan schedule:run; sleep 60; done` sẽ drift theo thời gian chạy command và có thể miss cadence theo phút ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:181)).
- Worker concurrency > 1 spawn nhiều `queue:work` child trong một shell, không có supervision riêng cho từng worker ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:175)).
- Worker mặc định `--max-jobs=0 --max-time=0`, thiếu recycle strategy để tự làm sạch memory leak ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:170)).
- `status_process()` có side-effect auto-heal, làm command status không còn read-only, dễ che dấu incident thật ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:398)).

## 2.4 Database Operations

Đánh giá: `Trung bình`

Vì sao:

- Phần DB credentials tương đối tốt: file `0600`, validate engine, validate db name, có MySQL + PostgreSQL support ([db_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/db_service.py:53), [db_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/db_service.py:108)).
- Có command backup/restore và test integration roundtrip trong CI cho cả MySQL và PostgreSQL ([ci.yml](/Volumes/Manager%20Data/Tool/larops/.github/workflows/ci.yml:48)).
- Nhưng chưa có:
  - retention
  - backup scheduler
  - checksum/integrity tracking
  - restore drill workflow
  - backup freshness check trong `doctor`
  - off-host replication/copy

## 2.5 SSL / Domain / Edge

Đánh giá: `Trung bình`

Vì sao:

- Có `ssl issue`, `ssl renew`, `ssl check`, auto-renew bằng systemd timer ([ssl.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/ssl.py:45), [ssl_auto_renew.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/ssl_auto_renew.py:91)).
- Có check hạn cert từ file PEM bằng `openssl` ([ssl_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/ssl_service.py:94)).
- Nhưng HTTP challenge hiện hard-code webroot `/var/www/html`, không map theo site root thật, nên rất dễ fail với host đa site Laravel ([ssl_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/ssl_service.py:47)).
- Repo không có Nginx vhost management, reverse proxy abstraction, CDN/WAF integration hay TLS failure alert riêng.

## 2.6 Architecture gaps

- Thiếu `shared` model hoàn chỉnh cho `.env`, `storage`, `bootstrap/cache`.
- Thiếu release hooks chuẩn cho build, migrate, cache warm, smoke test, queue restart.
- Thiếu chiến lược single-node vs multi-node; hiện tool nghiêng hẳn về single VPS.
- Thiếu cơ chế reconcile giữa runtime spec, release đang active, và systemd state sau deploy/rollback.
- Thiếu ownership rõ ràng cho Nginx site config, PHP-FPM tuning, Redis/MariaDB hardening.

# 3. Ops/SRE Review

## 3.1 Observability

Đánh giá: `Trung bình`

Vì sao:

- Có event stream JSONL, Telegram adapter, doctor command, security report, monitor scan/FIM ([events.py](/Volumes/Manager%20Data/Tool/larops/src/larops/core/events.py:7), [notify_systemd.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/notify_systemd.py:107), [doctor_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/doctor_service.py:43)).
- Có systemd timers cho SSL renew và monitor ([ssl_auto_renew.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/ssl_auto_renew.py:54), [monitor_systemd.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/monitor_systemd.py:66)).
- Nhưng chưa có:
  - metrics
  - queue lag monitoring
  - failed jobs monitoring
  - backup freshness monitoring
  - certificate fleet dashboard
  - tracing/error tracking integration
- `doctor` hiện quá nông, chủ yếu check binary/path/disk/app metadata ([doctor_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/doctor_service.py:43)).

## 3.2 Reliability

Đánh giá: `Kém`

Vì sao:

- Deploy không có pre/post gate và không gắn với runtime refresh.
- Scheduler có thể miss tick.
- Worker concurrency có thể degrade mà systemd không biết.
- `status` có thể tự restart service và che incident.
- Upgrade installer hiện không atomic.

## 3.3 Incident readiness

Đánh giá: `Trung bình`

Vì sao:

- Có event trail và notify path cơ bản, runbook production khá rõ ([PRODUCTION_RUNBOOK.md](/Volumes/Manager%20Data/Tool/larops/docs/PRODUCTION_RUNBOOK.md:1)).
- Có `site delete` guard + checkpoint trước purge ([site.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/site.py:219)).
- Nhưng checkpoint chưa có restore flow chính thức, nên readiness sự cố vẫn thiếu nửa còn lại.
- Không có incident summary command kiểu: queue lag, backup age, timer failures, SSL expiry fleet-wide.

## 3.4 Backup / Restore / Rollback readiness

Đánh giá: `Kém`

Vì sao:

- DB backup/restore chỉ là command thủ công.
- Site checkpoint có tạo nhưng không có restore command.
- App rollback chỉ đổi symlink, không rollback runtime state, không xử lý migration compatibility, không có post-rollback smoke test.

## 3.5 Runbook maturity

Đánh giá: `Trung bình`

Vì sao:

- Runbook có thật, khá cụ thể, có profile security monitor và upgrade procedure ([PRODUCTION_RUNBOOK.md](/Volumes/Manager%20Data/Tool/larops/docs/PRODUCTION_RUNBOOK.md:139)).
- Nhưng runbook đang phải lấp chỗ trống cho những capability chưa được tool enforce. Nói cách khác: operator đang gánh phần mà control plane đáng ra phải gánh.

## 3.6 Operational UX maturity

Đánh giá: `Tốt`

Vì sao:

- Pattern `plan -> apply` xuyên suốt repo ([README.md](/Volumes/Manager%20Data/Tool/larops/README.md:102)).
- Có `CommandLock` ở các flow phá hoại/race-prone.
- CLI output đơn giản, dễ automation bằng `--json`.
- Đây là phần mạnh nhất của LarOps hiện tại.

# 4. Security Review

## 4.1 Secret management

Đánh giá: `Trung bình`

Vì sao:

- DB credential files và Telegram secret files được ghi với mode chặt (`0600`) ([db_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/db_service.py:74), [alert_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/alert_service.py:14)).
- Config loader hỗ trợ secret file overrides và fail-fast khi file mất/rỗng ([config.py](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py:84)).
- Nhưng app secret management cho chính Laravel app gần như chưa có. Repo theo dõi `.env` bằng FIM, nhưng không có model provision/rotate/apply `.env` production-safe.
- `bootstrap init` có thể ghi lộ secret Telegram đã được resolve vào config YAML nếu bootstrap chạy với env/file override ([bootstrap.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/bootstrap.py:47), [config.py](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py:132)).

## 4.2 Host hardening

Đánh giá: `Trung bình`

Vì sao:

- Có UFW + Fail2ban + monitor scan/FIM + Telegram alert.
- Systemd units bật khá nhiều hardening flag như `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`, `UMask` ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:208), [notify_systemd.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/notify_systemd.py:53)).
- Nhưng bootstrap host chưa có hardening toàn diện cho nginx/php-fpm/db/redis, chưa có sudo boundary rõ ràng hay privilege review theo từng command.

## 4.3 Exposure & privilege boundaries

Đánh giá: `Trung bình`

Vì sao:

- Tool assume root/sudo để bootstrap/install và `www-data` cho app runtime. Đây là boundary hợp lý ở mức nền.
- Nhưng deploy hiện copy source local sang release mà không kiểm soát artifact integrity, shared env, hoặc site root mapping cho TLS.
- `site delete --no-prompt` vẫn đủ guard ở mức explicit operator intent, nhưng đây vẫn là lệnh rất mạnh nên cần runbook và checkpoint restore đi kèm.

## 4.4 Logging / audit / privacy risk

Đánh giá: `Trung bình`

Vì sao:

- Event stream JSONL tốt cho audit trail cơ bản.
- Nhưng chưa có retention/rotation/central shipping strategy do chính LarOps quản lý.
- Telegram adapter state handling không an toàn với log rotate nên có thể mất cảnh báo mà operator không biết ([telegram_adapter.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/telegram_adapter.py:123)).

## 4.5 Abuse detection / alerting / monitoring gaps

Đánh giá: `Trung bình`

Vì sao:

- Security module mới đủ cho baseline VPS: SSH brute-force, Nginx probe path, FIM.
- Chưa có application-level signals như login fail, invalid signature, webhook abuse, queue lag anomaly, cron death.
- `security report` là report hậu kiểm, chưa phải actionable alert engine hoàn chỉnh.

# 5. Arbiter Verdict

## 5.1 Xếp hạng tổng thể: D

LarOps chưa đạt mức “production control plane” cho Laravel production nghiêm túc. Mức hiện tại phù hợp hơn với staging hoặc single VPS có operator hiểu rõ từng giới hạn và chấp nhận bổ sung manual runbook.

## 5.2 3 rủi ro nguy hiểm nhất

1. Deploy/rollback không đủ semantics của Laravel production.
2. Scheduler/worker runtime có nguy cơ lỗi âm thầm.
3. Backup/alert/restore readiness chưa đủ để xử lý sự cố thật.

## 5.3 5 việc phải làm sớm nhất

1. Thiết kế lại release pipeline: build hooks, migrate hooks, shared state, queue restart, smoke gate.
2. Thay scheduler loop bằng model đúng hơn cho Laravel (`schedule:work` hoặc systemd timer/cron one-shot có khóa).
3. Tách supervision worker theo instance hoặc chuyển sang Horizon-ready topology; thêm recycle limits.
4. Bổ sung backup timer + retention + verify + restore drill + backup freshness checks.
5. Làm alert path rotation-safe và mở rộng `doctor` cho queue lag/timer/backup/SSL.

## 5.4 Quick wins

1. Bỏ side-effect auto-heal khỏi `status`.
2. Dùng `health_check_path` thật sự trong deploy/rollback.
3. Sửa `ssl issue` dùng webroot theo site thật.
4. Làm installer upgrade atomic.
5. Ngừng cài `supervisor` nếu không dùng, hoặc thực sự dùng nó.

## 5.5 Foundation fixes

1. Shared storage/env/bootstrap model cho Laravel releases.
2. Release hooks và release gates chuẩn hóa.
3. Runtime orchestration gắn với deploy/rollback.
4. DR story hoàn chỉnh: backup, checkpoint restore, restore drill.
5. Observability chuẩn SRE: metrics, lag, freshness, silent-failure detection.

# 6. Issue List

## [OPS-001] Deploy model chưa an toàn cho Laravel production
- Severity: Critical
- Category: Deploy
- Description: `app deploy` và `site create --deploy` chỉ copy source, switch symlink và cập nhật metadata; không có build/install/migrate/cache/smoke/restart runtime ([app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:188), [create.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/create.py:681)).
- Why it matters: Tool có thể báo deploy thành công trong khi release chưa runnable, chưa migrate, chưa warm cache và worker vẫn dùng code cũ.
- Suggested fix: Thêm pre/post deploy hooks chuẩn cho Laravel: `composer install --no-dev`, asset build/artifact, `artisan migrate --force`, cache warm, `queue:restart`, smoke check theo `health_check_path`.
- Affected areas: `app deploy`, `site create`, rollback semantics, runbook production.
- Validation / test needed: integration test end-to-end trên Ubuntu/systemd host giả lập; test deploy fail rollback; smoke test sau deploy.
- Suggested order: 1

## [OPS-002] Scheduler loop có thể trễ hoặc bỏ lịch
- Severity: Critical
- Category: Scheduler
- Description: Scheduler hiện chạy `while true; do php artisan schedule:run; sleep 60; done` ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:181)).
- Why it matters: Nếu `schedule:run` tốn thời gian, lần chạy tiếp theo bị trôi phút; appointment reminder, invoice jobs, cleanup jobs có thể chạy trễ hoặc bị miss.
- Suggested fix: Chuyển sang `php artisan schedule:work` nếu chấp nhận process lâu dài, hoặc systemd timer/cron one-shot mỗi phút với lock rõ ràng.
- Affected areas: `scheduler enable`, business-critical scheduled workloads.
- Validation / test needed: test runtime command generation; soak test 10-15 phút với scheduled task tốn thời gian; verify không drift.
- Suggested order: 2

## [OPS-003] Deploy/rollback không restart runtime hoặc queue state
- Severity: High
- Category: Runtime
- Description: `app deploy` và `app rollback` không chạm vào worker/scheduler/horizon runtime sau khi đổi release ([app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:188), [app.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/app.py:278)).
- Why it matters: Worker/Horizon có thể tiếp tục giữ code cũ trong memory; rollback có thể đổi symlink nhưng runtime vẫn chạy state cũ hoặc mới không khớp.
- Suggested fix: Thêm policy `post_deploy_runtime_action` và `post_rollback_runtime_action`; ít nhất hỗ trợ `queue:restart` và restart có chọn lọc worker/horizon.
- Affected areas: release safety, queue safety, rollback safety.
- Validation / test needed: test deploy/rollback với worker enabled; assert runtime được refresh đúng.
- Suggested order: 3

## [OPS-004] Thiếu shared state model cho `.env`, `storage`, `bootstrap/cache`
- Severity: High
- Category: Infrastructure
- Description: App init có tạo `shared/storage` và `shared/bootstrap`, nhưng deploy không symlink release vào shared paths ([app_lifecycle.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/app_lifecycle.py:50), [app_lifecycle.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/app_lifecycle.py:62)).
- Why it matters: File upload, generated cache và secret env không có lifecycle production-safe qua các release/rollback.
- Suggested fix: Thêm release preparation step để symlink `storage`, `bootstrap/cache`, `.env` và các shared asset khác trước khi switch `current`.
- Affected areas: filesystem strategy, rollback integrity, app secrets, uploads.
- Validation / test needed: integration test deploy -> upload/create file -> redeploy -> rollback; verify dữ liệu còn nguyên.
- Suggested order: 4

## [OPS-005] Worker concurrency hiện tại che mất lỗi child process và không có recycle limit
- Severity: High
- Category: Queue
- Description: Với `concurrency > 1`, LarOps spawn nhiều `queue:work` child dưới một shell `bash -lc`, đồng thời ép `--max-jobs=0 --max-time=0` ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:165)).
- Why it matters: Một child chết sẽ làm năng lực queue giảm âm thầm; worker chạy vô hạn làm memory leak tích tụ lâu dài.
- Suggested fix: Dùng systemd template unit cho từng worker instance hoặc khuyến nghị Horizon; thêm `--max-jobs`, `--max-time`, có thể thêm memory-aware restart strategy.
- Affected areas: queue throughput, memory stability, incident detection.
- Validation / test needed: test concurrency unit generation; kill một child và verify systemd nhận biết; soak test memory.
- Suggested order: 5

## [OPS-006] `status` command có side effect auto-heal
- Severity: High
- Category: Reliability
- Description: `status_process()` có thể tự restart service nếu thấy inactive và `auto_heal` bật ([runtime_process.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/runtime_process.py:433)).
- Why it matters: Lệnh status hoặc monitoring poll trở thành lệnh mutation, làm che incident, đổi timestamp và phá điều tra hậu sự cố.
- Suggested fix: Tách `status` thành read-only; tạo command riêng như `heal` hoặc `reconcile`; nếu cần auto-heal thì dùng watchdog/timer riêng.
- Affected areas: operator trust, incident forensics, monitoring semantics.
- Validation / test needed: test status không restart; test heal command riêng.
- Suggested order: 6

## [OPS-007] Backup/restore readiness chưa đủ cho DR thật
- Severity: High
- Category: Backup
- Description: Repo có `db backup` và `db restore`, nhưng chưa có timer/retention/checksum/freshness/restore drill của chính tool ([db.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/db.py:166)).
- Why it matters: “Có backup command” không đồng nghĩa “khôi phục được khi sự cố xảy ra”.
- Suggested fix: Thêm `db backup timer enable`, retention policy, metadata manifest, restore verify và `doctor backup` cho age/freshness.
- Affected areas: database ops, DR, runbook.
- Validation / test needed: restore drill định kỳ trên DB tạm; test retention; test doctor báo stale backup.
- Suggested order: 7

## [OPS-008] Telegram alert pipeline không an toàn với log rotation
- Severity: High
- Category: Observability
- Description: Adapter đọc event file theo `offset` và `sent_ids`, không theo inode/truncate ([telegram_adapter.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/telegram_adapter.py:123)).
- Why it matters: Sau log rotation/truncate, cảnh báo có thể im lặng mà daemon vẫn sống.
- Suggested fix: Bổ sung state gồm inode/dev/size và reset offset khi file đổi inode hoặc bị truncate; thêm self-alert khi offset invalid.
- Affected areas: Telegram notifications, incident alerting.
- Validation / test needed: test rotate/truncate events file; verify adapter tiếp tục gửi cảnh báo.
- Suggested order: 8

## [OPS-009] Installer upgrade đang phá hủy cài đặt cũ trước khi cài mới
- Severity: High
- Category: Reliability
- Description: Install script `rm -rf` install dir trước khi extract/cloning version mới ([install.sh](/Volumes/Manager%20Data/Tool/larops/scripts/install.sh:72), [install.sh](/Volumes/Manager%20Data/Tool/larops/scripts/install.sh:105)).
- Why it matters: Upgrade lỗi giữa chừng có thể làm mất working LarOps binary ngay trên host production.
- Suggested fix: Cài vào thư mục versioned tạm, validate xong rồi switch symlink atomically; giữ rollback path cho tool chính nó.
- Affected areas: upgrade, disaster during maintenance.
- Validation / test needed: integration test upgrade failure; verify old binary còn dùng được.
- Suggested order: 9

## [OPS-010] `health_check_path` tồn tại trong config nhưng chưa được dùng làm release gate
- Severity: High
- Category: Deploy
- Description: `health_check_path` có trong config mẫu nhưng deploy/rollback/doctor không dùng nó để smoke test ([config.py](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py:18), [README.md](/Volumes/Manager%20Data/Tool/larops/README.md:148)).
- Why it matters: Release có thể bị switch live mà không biết app thật sự lên được hay không.
- Suggested fix: Thêm HTTP smoke check configurable sau deploy/rollback và rollback-on-failure option.
- Affected areas: release gates, rollback confidence.
- Validation / test needed: test deploy hook gọi health endpoint giả; test auto rollback khi smoke fail.
- Suggested order: 10

## [OPS-011] Bootstrap/stack chỉ cài package, chưa cấu hình host; package set còn lệch capability
- Severity: Medium
- Category: Infrastructure
- Description: `stack install` chỉ chạy `apt-get install` cho package groups; không cấu hình service. Đồng thời cài `supervisor` nhưng repo không dùng, và không provision PostgreSQL dù DB tooling có hỗ trợ ([stack_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/stack_service.py:34)).
- Why it matters: Operator dễ hiểu nhầm “bootstrap xong là chạy production”, trong khi host chưa được cấu hình đầy đủ.
- Suggested fix: Tách package install khỏi host configure; thêm modules riêng cho nginx/php-fpm/db/redis hoặc document rõ “out of scope”; chỉnh package groups cho khớp capability.
- Affected areas: bootstrap init, infra assumptions, docs.
- Validation / test needed: end-to-end host bootstrap test; docs review.
- Suggested order: 11

## [OPS-012] SSL HTTP challenge webroot hard-code không khớp site root thật
- Severity: Medium
- Category: SSL
- Description: Certbot HTTP challenge dùng cố định `/var/www/html` ([ssl_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/ssl_service.py:47)).
- Why it matters: Trên host nhiều site hoặc Laravel site root riêng, cấp cert có thể fail hoặc cần cấu hình thủ công ngoài tool.
- Suggested fix: Cho phép derive webroot theo domain/current release hoặc yêu cầu path explicit.
- Affected areas: `ssl issue`, `site create -le`.
- Validation / test needed: test command build với custom webroot; docs update.
- Suggested order: 12

## [OPS-013] Checkpoint site delete chưa có restore workflow chính thức
- Severity: Medium
- Category: Backup
- Description: `site delete` tạo checkpoint tarball nhưng repo không có `site restore` hoặc restore runbook tương ứng ([site_delete.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/site_delete.py:29)).
- Why it matters: Checkpoint giúp an tâm giả; khi cần khôi phục thật operator vẫn phải tự phục dựng.
- Suggested fix: Thêm `site restore` và document restore drill; mở rộng coverage cho PostgreSQL credential nếu cần.
- Affected areas: destructive ops, DR.
- Validation / test needed: test purge -> restore -> app info/runtime restore.
- Suggested order: 13

## [OPS-014] `doctor` và observability hiện quá nông so với production ops
- Severity: Medium
- Category: Observability
- Description: `doctor` hiện chỉ check path, disk, binary, metadata/current/release ([doctor_service.py](/Volumes/Manager%20Data/Tool/larops/src/larops/services/doctor_service.py:43)).
- Why it matters: Nhiều failure mode quan trọng vẫn silent: queue lag, failed jobs, timer chết, stale backup, SSL sắp hết hạn, alert daemon stuck.
- Suggested fix: Mở rộng doctor thành health inventory: runtime active/enabled, timer freshness, backup age, SSL age, notify daemon state, monitor state.
- Affected areas: observability, incident response.
- Validation / test needed: test doctor report với mock stale states/timer failures.
- Suggested order: 14

## [OPS-015] `bootstrap init` có thể ghi lộ secret Telegram đã resolve vào YAML config
- Severity: Medium
- Category: Security
- Description: Config loader đọc secret file vào giá trị thật, và bootstrap writer dump cả `bot_token` lẫn `chat_id` vào config file mới ([config.py](/Volumes/Manager%20Data/Tool/larops/src/larops/config.py:132), [bootstrap.py](/Volumes/Manager%20Data/Tool/larops/src/larops/commands/bootstrap.py:47)).
- Why it matters: Secret vốn đang nằm ở secret file có thể bị hạ cấp thành plain text YAML.
- Suggested fix: Khi write default config, luôn blank-out secret values; chỉ giữ `_file` fields.
- Affected areas: bootstrap, secret hygiene.
- Validation / test needed: regression test bootstrap with env/file overrides; verify YAML không chứa secret value thật.
- Suggested order: 15

# 7. Thông tin còn thiếu làm giảm độ chính xác review

- Topology thật ngoài đời: có chạy single VPS hay tách web/db/redis không.
- Nginx vhost thực tế của các app consumer.
- Cách app consumer quản lý `.env`, uploads, shared storage hiện nay.
- Có logrotate hay journald policy thật cho `events.jsonl`, nginx access log, fail2ban log không.
- Có external monitoring nào bên ngoài LarOps không: Uptime Kuma, Sentry, Grafana, Better Stack, Cloudflare.
- Có restore drill thật sự đang chạy hay không.
- Có policy thực tế cho deploy artifact, composer/npm build, migrations và rollback của app consumer hay chưa.

# 8. Review File Output

File đề xuất lưu trong repo:

- `docs/reviews/modules/larops-production-readiness.md`

Nội dung file chính là tài liệu này.
