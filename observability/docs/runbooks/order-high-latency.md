# Runbook: HighOrderLatencyP95

**Alert:** `HighOrderLatencyP95`  
**Severity:** Warning (P99 > 2s = Critical)  
**Service:** Order Service  
**User Journey:** Create Order  
**Owner:** Backend Team

---

## 1. Triệu chứng

- p95 latency `/api/orders` > 500ms trong 5 phút
- Người dùng cảm nhận tạo đơn chậm
- Có thể chưa có error rate tăng (latency cao nhưng chưa timeout)

---

## 2. Kiểm tra

### Latency breakdown
```promql
# p50, p95, p99
histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket{service="order-service"}[5m])) by (le))
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="order-service"}[5m])) by (le))
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{service="order-service"}[5m])) by (le))
```

### Payment latency
```promql
histogram_quantile(0.95, sum(rate(payment_authorization_duration_seconds_bucket[5m])) by (le))
```

### Tìm trace chậm
Tempo → Search: service=`order-service` → sort by duration DESC

### Log theo trace chậm
```logql
{service_name=~"order-service|payment-service"} |= "<trace_id>"
```

---

## 3. Xác định bottleneck

| Span chậm nhất | Nguyên nhân | Workaround |
|---|---|---|
| `payment-service: authorize_payment` | Payment slow/timeout | Giảm timeout, bật fallback |
| `postgres: insert_order` | DB slow | Kiểm tra index, connection pool |
| `inventory-service: reserve_stock` | Inventory slow | Kiểm tra DB lock, cache |
| `redis: get` | Redis slow | Kiểm tra memory, connection |

---

## 4. Workaround

### Payment chậm
```bash
curl -X POST http://localhost:8081/admin/config \
  -H "Content-Type: application/json" \
  -d '{"payment_timeout_ms":1000,"max_retry":1,"payment_fallback_enabled":true}'
```

### Xóa fault (nếu là lab)
```bash
./scripts/trigger-incident.sh clear
```

### DB — thêm index (nếu slow query)
```sql
-- Kết nối vào postgres
docker exec -it postgres psql -U lab -d orders
-- Kiểm tra slow query
\timing on
EXPLAIN ANALYZE SELECT * FROM orders WHERE user_id='u001' ORDER BY created_at DESC;
-- Thêm index nếu thiếu
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
```

---

## 5. Escalation

- Nếu p99 > 2s → upgrade sang Critical
- Nếu latency không giảm sau workaround → escalate service owner
- Nếu có tăng error rate kèm theo → follow runbook `create-order-error-rate.md`
