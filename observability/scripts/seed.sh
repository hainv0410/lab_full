#!/usr/bin/env bash
# scripts/seed.sh — Seed dữ liệu mẫu cho lab
# Chạy: ./scripts/seed.sh

set -e

BASE_URL="${BASE_URL:-http://localhost:8080}"

echo "════════════════════════════════════"
echo "  Seeding lab data..."
echo "════════════════════════════════════"

# Wait for gateway
echo "⏳ Waiting for Gateway..."
for i in $(seq 1 20); do
  if curl -sf "${BASE_URL}/health" > /dev/null 2>&1; then
    echo "✅ Gateway is up"
    break
  fi
  echo "   Attempt $i/20..."
  sleep 3
done

# List products
echo ""
echo "📦 Products available:"
curl -s "${BASE_URL}/api/products" | python3 -m json.tool 2>/dev/null || \
  curl -s "${BASE_URL}/api/products"

# Send smoke order
echo ""
echo "🛒 Sending smoke order..."
RESP=$(curl -s -X POST "${BASE_URL}/api/orders" \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: seed-001" \
  -d '{
    "user_id": "u001",
    "product_id": "p001",
    "quantity": 1,
    "payment_method": "mock_card"
  }')

echo "Response: $RESP"
echo ""

ORDER_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('order_id','N/A'))" 2>/dev/null || echo "N/A")
echo "✅ Seed complete. Sample order_id: $ORDER_ID"
echo ""
echo "────────────────────────────────────────"
echo "Next steps:"
echo "  Grafana:      http://localhost:3000  (admin/admin)"
echo "  Prometheus:   http://localhost:9090"
echo "  Alertmanager: http://localhost:9093"
echo "  RabbitMQ:     http://localhost:15672 (lab/lab)"
echo "────────────────────────────────────────"
