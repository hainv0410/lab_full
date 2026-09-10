# Runbook: HighCreateOrderErrorRate

**Alert:** `HighCreateOrderErrorRate`  
**Severity:** Critical  
**Service:** Gateway / Order Service  
**User Journey:** Create Order  
**Owner:** SRE Team

---

## 1. Triệu chứng

- Error rate `/api/orders` > 5% trong 10 phút
- Alert firing trên Prometheus / Alertmanager
- Người dùng có thể không tạo được đơn hàng
- Có thể kèm p95/p99 latency tăng mạnh

---

## 2. Dashboard cần mở ngay

| Dashboard | URL |
|---|---|
| Retail Order Platform Overview | http://localhost:3000 |
| Prometheus Alerts | http://localhost:9090/alerts |
| Alertmanager | http://localhost:9093 |

---

## 3. Quy trình kiểm tra (thứ tự bắt buộc)

### Bước 1 — Xác nhận alert còn firing
```
http://localhost:9090/alerts
```
- Alert ở trạng thái `Firing` hay `Pending`?
- Alert bao lâu rồi?

### Bước 2 — Kiểm tra error rate và latency
PromQL trong Prometheus:
```promql
# Error rate
sum(rate(http_requests_total{route="/api/orders",status=~"5.."}[5m]))
/ sum(rate(http_requests_total{route="/api/orders"}[5m]))

# p95 latency
histogram_quantile(0.95,
  sum(rate(http_request_duration_seconds_bucket{service="order-service",route="/api/orders"}[5m])) by (le)
)
```

### Bước 3 — Tìm trace lỗi gần nhất
Loki query:
```logql
{service_name=~"gateway|order-service|inventory-service|payment-service"} |= "ERROR"
```
Lấy `trace_id` → mở Tempo.

### Bước 4 — Xem trace trên Tempo/Grafana
Grafana → Explore → Tempo → Search by trace_id  
Xác định span nào lỗi / chậm nhất.

### Bước 5 — Lọc log theo trace_id
```logql
{service_name=~"gateway|order-service|inventory-service|payment-service"} |= "<trace_id>"
```

### Bước 6 — Kiểm tra từng dependency

**Payment Service:**
```bash
curl http://localhost:8083/health
```
```logql
{service_name="payment-service"} |= "ERROR"
```

**Inventory Service:**
```bash
curl http://localhost:8082/health
```
```logql
{service_name="inventory-service"} |= "ERROR"
```

**PostgreSQL:** kiểm tra DB latency panel trên Grafana.

**Queue:** kiểm tra RabbitMQ backlog.

---

## 4. Workaround theo nguyên nhân

### Payment Timeout
```bash
# Bật fallback + giảm retry
curl -X POST http://localhost:8081/admin/config \
  -H "Content-Type: application/json" \
  -d '{"payment_timeout_ms":1500,"max_retry":1,"payment_fallback_enabled":true,"circuit_breaker_enabled":true}'

# Clear fault nếu là fault injection
./scripts/trigger-incident.sh clear
```

### Inventory Error
```bash
# Kiểm tra stock
curl http://localhost:8082/api/inventory/p001
# Restart nếu cần
docker compose restart inventory-service
```

### DB Lỗi
```bash
docker logs postgres --tail 100
docker compose restart postgres
```

### Retry Storm
```bash
# Giảm retry ngay lập tức
curl -X POST http://localhost:8081/admin/config \
  -H "Content-Type: application/json" \
  -d '{"max_retry":1,"payment_timeout_ms":1500,"circuit_breaker_enabled":true}'
```

---

## 5. Escalation

Escalate khi:
- SEV1: toàn bộ tạo đơn lỗi 100%
- Không giảm sau 15 phút workaround
- Có nghi ngờ mất / sai dữ liệu
- Có dấu hiệu bảo mật

---

## 6. Evidence cần lưu

- [ ] Ảnh alert trên Prometheus/Alertmanager
- [ ] Ảnh dashboard error rate
- [ ] Ảnh p95/p99 latency
- [ ] Trace ID lỗi mẫu + screenshot trace
- [ ] Log theo trace_id
- [ ] Lệnh workaround đã chạy
- [ ] Thời gian phục hồi

---

## 7. Sau phục hồi

1. Ghi timeline đầy đủ
2. Viết postmortem nếu SEV1/SEV2
3. Tạo action items có owner + deadline
4. Cập nhật runbook nếu thiếu bước
5. Cập nhật alert nếu cần điều chỉnh ngưỡng
6. Review error budget SLO
