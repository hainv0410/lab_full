# Retail Order Platform — Distributed Observability Lab

Khóa đào tạo: **Distributed Systems Engineering & Observability (DSEO)**  
5 ngày thực chiến với hệ thống microservices phân tán + full observability stack.

---

## Kiến trúc hệ thống

```
Client / Postman / k6
         │
    API Gateway :8080
         │
    Order Service :8081
     ├── Inventory Service :8082 ──► PostgreSQL :5432
     ├── Payment Service :8083   ──► PostgreSQL :5432
     ├── PostgreSQL :5432
     ├── Redis :6379
     └── Notification Service :8084
                    │
               RabbitMQ :5672
                    │
              Worker :8085
```

### Observability Stack
```
App Services (OTLP gRPC/HTTP)
         │
 OTel Collector :4317/:4318
     ├── Metrics → Prometheus :9090
     ├── Logs    → Loki :3100
     └── Traces  → Tempo :3200
                    │
                Grafana :3000
```

---

## Yêu cầu máy học viên

| Thành phần | Yêu cầu tối thiểu |
|---|---|
| RAM | 8 GB (khuyến nghị 12 GB) |
| CPU | 4 cores |
| Disk | 20 GB free |
| Docker | >= 24.x |
| Docker Compose | >= 2.x |
| Git | >= 2.x |
| k6 | >= 0.49 |
| curl | bất kỳ |

---

## Khởi động lab

### Bước 1 — Clone repo
```bash
git clone <repo-url>
cd distributed-observability-lab
```

### Bước 2 — Tạo file .env
```bash
# Linux/macOS
cp .env.example .env

# Windows
copy .env.example .env
```

### Bước 3 — Khởi động toàn bộ stack
```bash
docker compose up -d
```

### Bước 4 — Kiểm tra container
```bash
docker compose ps
```

### Bước 5 — Seed dữ liệu
```bash
# Linux/macOS
chmod +x scripts/*.sh
./scripts/seed.sh

# Windows
.\scripts-windows\seed.ps1
```

### Bước 6 — Health check
```bash
# Linux/macOS
./scripts/healthcheck.sh

# Windows
.\scripts-windows\healthcheck.ps1
```

---

## URLs sau khi khởi động

| Service | URL | Thông tin đăng nhập |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |
| Alertmanager | http://localhost:9093 | — |
| Loki (qua Grafana) | Grafana → Explore → Loki | — |
| Tempo (qua Grafana) | Grafana → Explore → Tempo | — |
| RabbitMQ | http://localhost:15672 | lab / lab |
| Gateway API | http://localhost:8080 | — |

---

## Kiểm tra port trước khi chạy

### Linux/macOS
```bash
lsof -i :3000 && lsof -i :8080 && lsof -i :9090 && lsof -i :3100
```

### Windows
```powershell
netstat -ano | findstr ":3000"
netstat -ano | findstr ":8080"
netstat -ano | findstr ":9090"
netstat -ano | findstr ":3100"
```

---

## Gửi request tạo đơn

### Linux/macOS — curl
```bash
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-001" \
  -d '{
    "user_id": "u001",
    "product_id": "p001",
    "quantity": 2,
    "payment_method": "mock_card"
  }'
```

### Windows — PowerShell
```powershell
$body = @{
  user_id        = "u001"
  product_id     = "p001"
  quantity       = 2
  payment_method = "mock_card"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8080/api/orders" `
  -Headers @{ "X-Request-ID" = "demo-001" } `
  -ContentType "application/json" `
  -Body $body
```

---

## Truy vấn Loki (Day 2)

```logql
# Log theo service
{service_name="order-service"}

# Log theo request_id
{service_name="order-service"} |= "demo-001"

# Log lỗi toàn hệ thống
{service_name=~"gateway|order-service|inventory-service|payment-service"} |= "ERROR"

# Parse JSON và lọc theo field
{service_name="order-service"} | json | status_code=500

# Lọc nhiều service theo request
{service_name=~"gateway|order-service|inventory-service|payment-service|notification-service"} |= "demo-001"
```

---

## Incident Simulation (Day 4/5)

```bash
# Payment timeout
./scripts/trigger-incident.sh payment-timeout

# Retry storm
./scripts/trigger-incident.sh retry-storm

# Queue backlog
./scripts/trigger-incident.sh queue-backlog

# Clear all faults
./scripts/trigger-incident.sh clear

# Apply workaround
./scripts/trigger-incident.sh workaround-payment
```

---

## Load Test (Day 5)

```bash
# Smoke test (1 VU, 30s)
k6 run load-test/k6-smoke-test.js

# Baseline (ramp 5→10 VU, 3m)
k6 run load-test/k6-create-order.js

# Custom VU/duration
k6 run --vus 10 --duration 3m load-test/k6-create-order.js

# Stress test (ramp 5→80 VU)
k6 run load-test/k6-stress-test.js
```

---

## Reset lab

```bash
# Soft reset (giữ volumes)
docker compose down
docker compose up -d
./scripts/seed.sh

# Full reset (xóa data)
docker compose down -v
docker compose up -d
./scripts/seed.sh
```

---

## Cấu trúc thư mục

```
distributed-observability-lab/
├── apps/
│   ├── gateway/              # Entry point, reverse proxy
│   ├── order-service/        # Core business logic
│   ├── inventory-service/    # Stock management
│   ├── payment-service/      # Payment auth + fault injection
│   ├── notification-service/ # Queue publisher
│   └── worker/               # RabbitMQ consumer
├── infra/
│   ├── otel-collector/       # OTel Collector config
│   ├── prometheus/           # Prometheus + alert rules
│   ├── grafana/              # Dashboards + provisioning
│   ├── loki/                 # Log aggregation
│   ├── tempo/                # Distributed tracing
│   └── alertmanager/         # Alert routing
├── load-test/
│   ├── k6-smoke-test.js
│   ├── k6-create-order.js    # Baseline load test
│   └── k6-stress-test.js
├── scripts/                  # Linux/macOS scripts
├── scripts-windows/          # Windows PowerShell scripts
├── docs/runbooks/            # Runbooks cho alert
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Lỗi thường gặp

### Docker không đủ RAM
```bash
# Kiểm tra
docker stats

# Giảm tải: tắt cadvisor/node-exporter nếu không cần
docker compose stop cadvisor node-exporter
```

### Port bị chiếm
```bash
# Tìm process dùng port 3000
lsof -i :3000         # Linux/macOS
netstat -ano | findstr ":3000"  # Windows
```

### Loki không nhận log
```bash
docker logs otel-collector --tail 50
docker logs loki --tail 50
```

### Trace không xuất hiện
```bash
docker logs otel-collector --tail 50 | grep -i tempo
docker logs tempo --tail 50
```

### Service restart liên tục
```bash
docker logs <service-name> --tail 100
# Thường do DB/RabbitMQ chưa sẵn sàng → đợi 1-2 phút
```

---

## Tài liệu SOP theo ngày

| Ngày | Chủ đề | SOP Key |
|---|---|---|
| Day 1 | Foundations & Observability Baseline | SOP-01, SOP-02, SOP-03 |
| Day 2 | Logging, Tracing & Correlation | SOP-03→07 |
| Day 3 | SLI, SLO & Alert Engineering | SOP-06→09 |
| Day 4 | Incident Response & Postmortem | SOP-09→12 |
| Day 5 | Capacity Planning & Production Readiness | SOP-11→14 |
