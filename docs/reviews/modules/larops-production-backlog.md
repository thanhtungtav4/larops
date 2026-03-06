# LarOps Production Implementation Backlog

Ngày tạo: 2026-03-06
Nguồn: [larops-production-readiness.md](/Volumes/Manager%20Data/Tool/larops/docs/reviews/modules/larops-production-readiness.md)

## Mục tiêu backlog

Chuyển các finding `OPS-001` đến `OPS-015` thành backlog implementation có thể thực thi theo thứ tự ưu tiên production:

1. Chặn downtime và false-success trước.
2. Ổn định deploy/runtime/backup/alert path trước.
3. Sau đó mới dọn platform gap, security hygiene và docs.

## Nguyên tắc sắp thứ tự

- `Critical`: xử lý trước, vì đang chặn production readiness.
- `High`: xử lý ngay sau khi chốt deploy/runtime model.
- `Medium`: xử lý trong wave hardening và productization.

## Thứ tự thực hiện đề xuất

1. `OPS-001` Deploy model chưa an toàn cho Laravel production
2. `OPS-002` Scheduler loop có thể trễ hoặc bỏ lịch
3. `OPS-003` Deploy/rollback không restart runtime hoặc queue state
4. `OPS-004` Thiếu shared state model cho `.env`, `storage`, `bootstrap/cache`
5. `OPS-005` Worker concurrency hiện tại che mất lỗi child process và không có recycle limit
6. `OPS-006` `status` command có side effect auto-heal
7. `OPS-007` Backup/restore readiness chưa đủ cho DR thật
8. `OPS-008` Telegram alert pipeline không an toàn với log rotation
9. `OPS-009` Installer upgrade đang phá hủy cài đặt cũ trước khi cài mới
10. `OPS-010` `health_check_path` tồn tại trong config nhưng chưa được dùng làm release gate
11. `OPS-011` Bootstrap/stack chỉ cài package, chưa cấu hình host; package set còn lệch capability
12. `OPS-012` SSL HTTP challenge webroot hard-code không khớp site root thật
13. `OPS-013` Checkpoint site delete chưa có restore workflow chính thức
14. `OPS-014` `doctor` và observability hiện quá nông so với production ops
15. `OPS-015` `bootstrap init` có thể ghi lộ secret Telegram đã resolve vào YAML config

## Wave 0: Critical

### [OPS-001] Deploy model chưa an toàn cho Laravel production
- Priority: P0
- Outcome cần đạt:
  - LarOps có release pipeline đúng ngữ nghĩa Laravel production.
  - `deploy thành công` đồng nghĩa release mới thực sự runnable.
- Dependency:
  - Không phụ thuộc issue khác.
- Implementation tasks:
  1. Thiết kế `release hooks` với các phase cố định: `prepare`, `build`, `activate`, `post-activate`, `verify`.
  2. Bổ sung config cho deploy hooks và timeout từng phase.
  3. Thêm bước `composer install --no-dev --optimize-autoloader` theo artifact/source mode.
  4. Thêm tùy chọn asset strategy: `skip`, `npm build`, `artifact-only`.
  5. Thêm bước Laravel cache warm: `config:cache`, `route:cache`, `view:cache`, `event:cache` nếu chọn.
  6. Bổ sung `migrate --force` strategy với `pre-activate` hoặc `post-activate` mode.
  7. Ghi release manifest đầy đủ vào từng release.
  8. Nếu phase fail trước `activate`, release không được switch live.
  9. Nếu fail sau `activate`, phải emit failure rõ ràng và hỗ trợ rollback tự động có điều kiện.
- Deliverables:
  - `app deploy` mới
  - release hook config schema
  - release manifest schema
  - docs deploy flow mới
- Definition of done:
  - Có test deploy success/fail qua từng phase.
  - Có smoke test end-to-end với app Laravel fixture tối thiểu.
  - Operator nhìn vào output biết release fail ở phase nào.

### [OPS-002] Scheduler loop có thể trễ hoặc bỏ lịch
- Priority: P0
- Outcome cần đạt:
  - Scheduler semantics đúng với production Laravel.
- Dependency:
  - Nên làm song song với `OPS-001`, nhưng merge trước khi chốt runtime model mới.
- Implementation tasks:
  1. Bỏ model `while true; schedule:run; sleep 60`.
  2. Chọn một trong hai mode chính:
     - `schedule:work` long-running
     - systemd timer one-shot mỗi phút
  3. Nếu dùng timer one-shot: thêm lock để tránh overlap.
  4. Nếu dùng `schedule:work`: thêm restart/recycle policy rõ ràng.
  5. Cập nhật runbook để operator hiểu mode mới.
  6. Thêm cảnh báo nếu scheduler bị disable hoặc timer/service inactive.
- Deliverables:
  - runtime scheduler command generation mới
  - test command/unit generation
  - runbook scheduler production mode
- Definition of done:
  - Không còn drift do `sleep 60`.
  - Có test mô phỏng task chạy lâu nhưng không làm miss cadence thiết kế.

## Wave 1: High

### [OPS-003] Deploy/rollback không restart runtime hoặc queue state
- Priority: P1
- Outcome cần đạt:
  - Sau deploy/rollback, runtime state khớp release state.
- Dependency:
  - Phụ thuộc `OPS-001`.
- Implementation tasks:
  1. Thêm runtime reconciliation step sau deploy/rollback.
  2. Hỗ trợ `queue:restart` cho worker/horizon.
  3. Cho phép chọn policy: `none`, `queue-restart`, `restart-services`, `rolling`.
  4. Emit event riêng cho runtime refresh outcome.
  5. Rollback phải support refresh runtime theo target release.
- Deliverables:
  - runtime refresh service/helper
  - config deploy runtime action
  - tests deploy/rollback + worker/horizon
- Definition of done:
  - Deploy/rollback không để worker giữ code cũ.
  - Có log/event rõ runtime nào đã được refresh.

### [OPS-004] Thiếu shared state model cho `.env`, `storage`, `bootstrap/cache`
- Priority: P1
- Outcome cần đạt:
  - Release mới và release cũ cùng dùng shared state đúng chuẩn Laravel.
- Dependency:
  - Phụ thuộc `OPS-001`.
- Implementation tasks:
  1. Thiết kế shared layout chính thức cho app root.
  2. Thêm step `prepare_shared_paths`.
  3. Symlink `storage` và `bootstrap/cache` vào release trước khi activate.
  4. Support `.env` shared file hoặc explicit env-file strategy.
  5. Bổ sung validation khi shared target thiếu.
  6. Cập nhật `site permissions` để áp mode đúng cho shared paths.
- Deliverables:
  - shared path service
  - deploy integration với shared paths
  - docs về shared model
- Definition of done:
  - Redeploy/rollback không mất upload, cache writable path vẫn đúng.
  - Nếu `.env` thiếu thì deploy fail sớm, không switch live.

### [OPS-005] Worker concurrency hiện tại che mất lỗi child process và không có recycle limit
- Priority: P1
- Outcome cần đạt:
  - Queue runtime có supervision đúng và predictable.
- Dependency:
  - Nên làm sau `OPS-003`.
- Implementation tasks:
  1. Bỏ model nhiều child trong một shell nếu `concurrency > 1`.
  2. Thiết kế systemd template unit hoặc nhiều service unit riêng theo worker index.
  3. Thêm recycle defaults: `--max-jobs`, `--max-time`, có thể thêm `--memory` nếu dùng Horizon.
  4. Tách mode `queue:work` và `horizon` rõ hơn trong UX.
  5. Thêm `worker scale` hoặc `concurrency` reconciliation command nếu cần.
- Deliverables:
  - unit rendering mới cho worker
  - runtime spec mới cho worker replicas
  - tests cho multi-worker
- Definition of done:
  - Một worker chết không làm LarOps báo healthy giả.
  - Có recycle strategy mặc định hợp lý cho production.

### [OPS-006] `status` command có side effect auto-heal
- Priority: P1
- Outcome cần đạt:
  - `status` là read-only 100%.
- Dependency:
  - Có thể làm độc lập.
- Implementation tasks:
  1. Tách logic auto-heal ra khỏi `status_process()`.
  2. Tạo command riêng như `worker heal`, `scheduler heal`, `horizon heal` hoặc `runtime reconcile`.
  3. Nếu muốn auto-heal tự động, đặt trong timer/service riêng.
  4. Cập nhật test để đảm bảo `status` không mutation state.
- Deliverables:
  - runtime status refactor
  - heal/reconcile command mới
  - regression tests
- Definition of done:
  - Gọi `status` nhiều lần không đổi spec, không restart service.

### [OPS-007] Backup/restore readiness chưa đủ cho DR thật
- Priority: P1
- Outcome cần đạt:
  - Backup có lịch, retention, freshness check và restore drill cơ bản.
- Dependency:
  - Có thể làm song song với `OPS-006`.
- Implementation tasks:
  1. Thêm metadata file cho mỗi backup: engine, db, timestamp, size, sha256.
  2. Thêm retention policy theo count/age.
  3. Thêm systemd timer cho backup jobs.
  4. Thêm `db backup status` hoặc `doctor` check freshness.
  5. Thêm `db restore verify` hoặc restore drill mode vào database tạm.
  6. Emit events cho backup start/completed/failed/stale.
- Deliverables:
  - backup manifest
  - backup timer/service
  - retention pruning
  - doctor backup freshness
- Definition of done:
  - Có thể chứng minh backup gần nhất còn mới và restore được.

### [OPS-008] Telegram alert pipeline không an toàn với log rotation
- Priority: P1
- Outcome cần đạt:
  - Event notifier không mất cảnh báo sau rotate/truncate.
- Dependency:
  - Không phụ thuộc issue khác.
- Implementation tasks:
  1. Mở rộng state file: `inode`, `device`, `offset`, `updated_at`.
  2. Khi file đổi inode hoặc bị truncate, reset offset hợp lệ.
  3. Thêm self-diagnostic trong report output.
  4. Có thể thêm event `notify.telegram.state_reset`.
- Deliverables:
  - adapter state schema mới
  - migration compatibility với state cũ
  - tests rotate/truncate
- Definition of done:
  - Rotate file không làm daemon “sống giả, mất alert thật”.

### [OPS-009] Installer upgrade đang phá hủy cài đặt cũ trước khi cài mới
- Priority: P1
- Outcome cần đạt:
  - Upgrade LarOps atomic và có rollback path.
- Dependency:
  - Không phụ thuộc issue khác.
- Implementation tasks:
  1. Chuyển installer sang layout versioned, ví dụ `/opt/larops/releases/<version>`.
  2. Tạo symlink `/opt/larops/current`.
  3. Chỉ switch symlink sau khi extract + venv + CLI smoke xong.
  4. Giữ lại version trước đó để rollback tool.
  5. Thêm cleanup policy cho old releases.
- Deliverables:
  - install/upgrade script mới
  - rollback install procedure
  - docs upgrade an toàn
- Definition of done:
  - Upgrade fail không làm mất LarOps đang chạy.

### [OPS-010] `health_check_path` tồn tại trong config nhưng chưa được dùng làm release gate
- Priority: P1
- Outcome cần đạt:
  - Release có smoke gate thật.
- Dependency:
  - Phụ thuộc `OPS-001`.
- Implementation tasks:
  1. Thêm HTTP health check helper.
  2. Hỗ trợ config timeout/retries/expected status.
  3. Chạy smoke sau activate và sau rollback.
  4. Hỗ trợ `rollback_on_healthcheck_fail`.
  5. Emit event riêng cho health check result.
- Deliverables:
  - health check service
  - deploy/rollback integration
  - test endpoint success/fail
- Definition of done:
  - Deploy success chỉ khi health gate pass.

## Wave 2: Medium

### [OPS-011] Bootstrap/stack chỉ cài package, chưa cấu hình host; package set còn lệch capability
- Priority: P2
- Outcome cần đạt:
  - Bootstrap phản ánh đúng capability thật của LarOps.
- Dependency:
  - Không chặn P0/P1.
- Implementation tasks:
  1. Quyết định rõ phạm vi bootstrap: chỉ install package hay cả configure host.
  2. Nếu chỉ install package, đổi tên/help/docs cho rõ.
  3. Nếu configure host, thêm module cấu hình nginx/php-fpm/redis/mariadb cơ bản.
  4. Bỏ `supervisor` khỏi stack hoặc thực sự hỗ trợ nó.
  5. Cân nhắc package group cho PostgreSQL.
- Deliverables:
  - stack/bootstrap spec rõ ràng
  - docs và help text cập nhật
- Definition of done:
  - Operator không còn hiểu nhầm `bootstrap init` là host production-ready đầy đủ.

### [OPS-012] SSL HTTP challenge webroot hard-code không khớp site root thật
- Priority: P2
- Outcome cần đạt:
  - Certbot issue hoạt động đúng với site root thực tế.
- Dependency:
  - Nên làm sau `OPS-004`.
- Implementation tasks:
  1. Thêm `--webroot-path` explicit.
  2. Nếu có domain app đã đăng ký, derive path từ app/site layout.
  3. Validate path tồn tại trước khi issue cert.
  4. Cập nhật `site create -le`.
- Deliverables:
  - ssl command builder mới
  - docs HTTP challenge multi-site
- Definition of done:
  - Không còn hard-code `/var/www/html` là mặc định duy nhất.

### [OPS-013] Checkpoint site delete chưa có restore workflow chính thức
- Priority: P2
- Outcome cần đạt:
  - Checkpoint có khả năng phục hồi thực tế.
- Dependency:
  - Nên làm sau `OPS-004` và `OPS-007`.
- Implementation tasks:
  1. Thiết kế `site restore`.
  2. Support restore metadata, app root, runtime spec, optional secrets.
  3. Validate target domain trống hoặc explicit `--force`.
  4. Emit restore events.
  5. Thêm restore runbook.
- Deliverables:
  - `site restore` command
  - tests delete -> restore
  - docs restore drill
- Definition of done:
  - Checkpoint không còn chỉ là archive “để đó”.

### [OPS-014] `doctor` và observability hiện quá nông so với production ops
- Priority: P2
- Outcome cần đạt:
  - `doctor` và status surface đủ tín hiệu production.
- Dependency:
  - Nên làm sau `OPS-007`, `OPS-008`, `OPS-010`.
- Implementation tasks:
  1. Thêm check runtime service active/enabled.
  2. Thêm check timer state cho SSL/monitor/backup.
  3. Thêm check backup freshness.
  4. Thêm check notify daemon state.
  5. Thêm check FIM baseline tồn tại.
  6. Thêm check security/monitor report summary gần nhất nếu có.
- Deliverables:
  - doctor service mở rộng
  - optional JSON summary hữu ích cho automation
- Definition of done:
  - `doctor` đủ để nhìn ra các failure mode silent phổ biến.

### [OPS-015] `bootstrap init` có thể ghi lộ secret Telegram đã resolve vào YAML config
- Priority: P2
- Outcome cần đạt:
  - Bootstrap không bao giờ materialize secret thật vào config file.
- Dependency:
  - Có thể làm độc lập, nên làm sớm vì nhỏ.
- Implementation tasks:
  1. Tách `config dump for bootstrap` khỏi runtime config object đã resolve secret.
  2. Khi ghi config, luôn blank các field secret value.
  3. Chỉ giữ secret file path.
  4. Thêm regression test.
- Deliverables:
  - bootstrap config writer fix
  - test bảo vệ secret hygiene
- Definition of done:
  - Dù secret đã có trong env/file override, config output vẫn không chứa secret value thật.

## Milestone đề xuất

### Milestone A: Deploy Safety Baseline
- Bao gồm: `OPS-001`, `OPS-002`, `OPS-003`, `OPS-010`
- Kết quả mong muốn:
  - Deploy có gate, rollback có nghĩa, scheduler không drift.

### Milestone B: Runtime + State Integrity
- Bao gồm: `OPS-004`, `OPS-005`, `OPS-006`
- Kết quả mong muốn:
  - Runtime khớp release, worker stable hơn, status không mutation.

### Milestone C: Backup + Alert Reliability
- Bao gồm: `OPS-007`, `OPS-008`, `OPS-013`, `OPS-014`
- Kết quả mong muốn:
  - Có DR story tối thiểu và alert path bền hơn.

### Milestone D: Platform/Productization Cleanup
- Bao gồm: `OPS-009`, `OPS-011`, `OPS-012`, `OPS-015`
- Kết quả mong muốn:
  - Installer an toàn hơn, bootstrap ít gây hiểu lầm hơn, secret hygiene sạch hơn.

## Backlog execution notes

- Nếu nguồn lực ít, không nên làm `OPS-011` trước `OPS-001`.
- Nếu mục tiêu là “ship được single VPS production an toàn hơn trong 2 sprint”, chỉ nên chốt:
  - `OPS-001`
  - `OPS-002`
  - `OPS-003`
  - `OPS-004`
  - `OPS-007`
  - `OPS-008`
  - `OPS-010`
- Nếu muốn nâng LarOps lên “serious production candidate”, phải hoàn thành ít nhất Milestone A + B + C.
