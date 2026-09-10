# DSEO Lab — Hướng dẫn chạy trên Windows (từ con số 0)

## 0. Bản vá đã áp dụng trong gói này
- **requirements.txt (6 service)**: thêm `setuptools<81` — sửa lỗi `ModuleNotFoundError: No module named 'pkg_resources'` khiến **mọi service crash-loop**.
- **scripts-windows/seed.ps1, healthcheck.ps1**: thay toán tử `??` (chỉ có ở PowerShell 7) bằng cú pháp chạy được cả trên Windows PowerShell 5.1.
- Dọn `__pycache__` và thư mục rác do brace-expansion.

## 1. Cài công cụ (1 lần)
1. **Docker Desktop for Windows** (bật WSL2 backend). Mở Docker Desktop, đợi Engine = running.
   - Settings → Resources: để **>= 8 GB RAM** (khuyến nghị 12 GB), nếu không Grafana/Loki/Tempo dễ bị OOM.
2. **PowerShell 7+** (khuyến nghị): `winget install Microsoft.PowerShell`, rồi mở **"PowerShell 7"** (lệnh `pwsh`).
   - Nếu vẫn dùng Windows PowerShell 5.1 mặc định thì các script đã vá vẫn chạy được.
3. (Tuỳ chọn) **k6** cho Day 5: `winget install k6 --source winget`.

## 2. Chuẩn bị
```powershell
cd <thư-mục-lab>          # nơi có docker-compose.yml
copy .env.example .env
```

## 3. Khởi động
```powershell
docker compose build       # build 6 app image (lần đầu ~vài phút)
docker compose up -d
docker compose ps          # chờ tới khi healthy
```
> Lần đầu các app có thể restart 1-2 lần trong lúc Postgres/RabbitMQ khởi động — bình thường. Đợi ~1-2 phút.

## 4. Seed + Health check
```powershell
.\scripts-windows\seed.ps1
.\scripts-windows\healthcheck.ps1
```
Mong đợi: `seed.ps1` trả về `order_id` + `trace_id`; `healthcheck.ps1` tất cả ✅.

## 5. URL
| Service | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |
| Alertmanager | http://localhost:9093 | — |
| RabbitMQ | http://localhost:15672 | lab / lab |
| Gateway API | http://localhost:8080 | — |

## 6. Tạo đơn thử
```powershell
$body = @{ user_id="u001"; product_id="p001"; quantity=2; payment_method="mock_card" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/orders" `
  -Headers @{ "X-Request-ID" = "demo-001" } -ContentType "application/json" -Body $body
```

## Lưu ý Windows hay gặp
- **Port bị chiếm**: `netstat -ano | findstr ":3000"` → đổi port trong compose hoặc tắt app chiếm port.
- **"running scripts is disabled"**: chạy 1 lần `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **cadvisor / node-exporter**: mount `/proc`, `/sys`, `/var/lib/docker` kiểu Linux — trên Docker Desktop/WSL2 thường vẫn chạy nhưng node/host metrics có thể không đầy đủ. Không ảnh hưởng luồng Create Order; nếu thiếu RAM có thể tắt: `docker compose stop cadvisor node-exporter`.

## Bản vá source bổ sung (turn 2)
- **6× apps/*/main.py**: thêm OTLP **log export** → Collector → Loki. Trước đây app chỉ xuất trace; giờ log JSON cũng được đẩy qua OTLP nên **Day 2 (Loki) có log thật**, query `{service_name="order-service"}` hoạt động.
- **infra/otel-collector/config.yaml**: thêm processor `resource/loki` để gắn nhãn Loki `service_name`/`service_namespace` từ resource.
- **apps/order-service/main.py**: thêm metric `asyncpg_pool_size{service="order-service"}` → alert `DatabaseConnectionPoolHigh` firing được.
- **infra/prometheus/prometheus.yml**: job rabbitmq scrape `/metrics/per-object` để có `rabbitmq_queue_messages_ready{queue="notifications"}` → alert `NotificationQueueBacklog` + bài queue-backlog Day 4 hoạt động.

> Lưu ý: các fix log→Loki đã được kiểm chứng ở mức import + phát log record; việc hiển thị nhãn cuối cùng trong Grafana nên xác nhận lại lần chạy đầu (mở Explore → Loki, chọn time range 15 phút, gửi 1 request rồi query `{service_name="order-service"}`).
