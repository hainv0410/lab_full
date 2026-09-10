# Runbook: NotificationQueueBacklog

**Alert:** `NotificationQueueBacklog`  
**Severity:** Warning  
**Service:** Worker / Notification Service  
**User Journey:** Async Notification  
**Owner:** Platform Team

---

## 1. Triệu chứng

- Queue `notifications` depth > 1000 messages trong 5 phút
- **QUAN TRỌNG:** API `/api/orders` vẫn có thể trả 200 — HTTP 200 không đồng nghĩa business outcome thành công!
- Notification không đến tay người dùng
- Worker throughput thấp

---

## 2. Kiểm tra nhanh

### Queue depth
```promql
rabbitmq_queue_messages_ready{queue="notifications"}
```

### Worker throughput
```promql
rate(notification_jobs_completed_total[5m])
rate(notification_jobs_processed_total{status="error"}[5m])
```

### Worker logs
```logql
{service_name="worker"} |= "ERROR"
{service_name="worker"} |= "notification"
```

### Worker health
```bash
curl http://localhost:8085/health
```

---

## 3. Xác định nguyên nhân

| Triệu chứng | Nguyên nhân khả năng |
|---|---|
| Worker health DOWN | Worker crash |
| Worker UP nhưng throughput ~0 | Slow consumer / blocking |
| Error rate worker cao | Processing error / poison message |
| Queue tăng + worker bình thường | Produce rate quá cao |
| DLQ tăng | Poison message |

---

## 4. Workaround

### Worker chết — restart
```bash
docker compose restart worker
docker logs worker --tail 100
```

### Slow consumer fault — clear
```bash
./scripts/trigger-incident.sh clear
# hoặc Windows:
.\scripts-windows\trigger-incident.ps1 clear
```

### Scale worker
```bash
docker compose up -d --scale worker=3
```

### Kiểm tra DLQ (nếu có)
RabbitMQ Management: http://localhost:15672  
User: lab / Pass: lab  
→ Queues → kiểm tra `notifications.dlq`

---

## 5. Theo dõi phục hồi

```promql
# Queue drain rate
rate(rabbitmq_queue_messages_ready{queue="notifications"}[5m])

# Worker throughput tăng lại?
rate(notification_jobs_completed_total[5m])
```

Hệ thống phục hồi khi:
- Queue depth giảm đều
- Worker throughput trở lại bình thường
- Alert resolved trong Alertmanager

---

## 6. Action items sau sự cố

- [ ] Thêm alert `backlog_age` (thời gian message nằm chờ)
- [ ] Cấu hình DLQ để tránh poison message block queue
- [ ] Thêm panel worker throughput vào dashboard chính
- [ ] Xem xét thêm SLI: `99% notification processed within 60s`
- [ ] Review consumer prefetch count
