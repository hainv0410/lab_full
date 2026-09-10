#!/usr/bin/env bash
# scripts/healthcheck.sh — Kiểm tra health toàn hệ thống
# Chạy: ./scripts/healthcheck.sh

BASE_URL="${BASE_URL:-http://localhost:8080}"
PASS=0; FAIL=0

check_url() {
  local name="$1"
  local url="$2"
  local expected="${3:-200}"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url")
  if [ "$code" = "$expected" ]; then
    echo "  ✅ $name ($url) → $code"
    PASS=$((PASS+1))
  else
    echo "  ❌ $name ($url) → $code (expected $expected)"
    FAIL=$((FAIL+1))
  fi
}

echo ""
echo "════════════════════════════════════════════"
echo "  Health Check — Retail Order Platform Lab"
echo "════════════════════════════════════════════"

echo ""
echo "── Application Services ─────────────────────"
check_url "Gateway"              "${BASE_URL}/health"
check_url "Order Service"        "http://localhost:8081/health"
check_url "Inventory Service"    "http://localhost:8082/health"
check_url "Payment Service"      "http://localhost:8083/health"
check_url "Notification Service" "http://localhost:8084/health"
check_url "Worker"               "http://localhost:8085/health"

echo ""
echo "── Observability Stack ──────────────────────"
check_url "Prometheus"    "http://localhost:9090/-/healthy"
check_url "Grafana"       "http://localhost:3000/api/health"
check_url "Alertmanager"  "http://localhost:9093/-/healthy"
check_url "Loki"          "http://localhost:3100/ready"
check_url "Tempo"         "http://localhost:3200/ready"

echo ""
echo "── Infrastructure ───────────────────────────"
check_url "RabbitMQ Mgmt" "http://localhost:15672"

echo ""
echo "════════════════════════════════════════════"
echo "  Result: $PASS passed, $FAIL failed"
echo "════════════════════════════════════════════"

# Quick smoke request
echo ""
echo "── Smoke Request ────────────────────────────"
RESP=$(curl -s -X POST "${BASE_URL}/api/orders" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: hc-$(date +%s)" \
  -d '{"user_id":"u001","product_id":"p001","quantity":1,"payment_method":"mock_card"}' \
  --connect-timeout 5 --max-time 10)

if echo "$RESP" | grep -q "order_id"; then
  echo "  ✅ Smoke request: order created"
  echo "  $(echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP")"
else
  echo "  ❌ Smoke request failed: $RESP"
fi

echo ""
[ $FAIL -eq 0 ] && echo "✅ All checks passed!" || echo "⚠️  $FAIL check(s) failed — review above"
